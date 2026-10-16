"""Post-classifier enrichment: intent pipeline + planner orchestration."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.planning.observability import log_planner_event
from services.planning.resource_catalog import ResourceCatalog


logger = logging.getLogger(__name__)


async def apply_post_classification_pipeline(
    classification_result: Dict[str, Any],
    *,
    user_id: str,
    prompt: str,
    resource_catalog: ResourceCatalog,
    intent_state_for_classifier: Optional[Dict[str, Any]],
    conversation_history: Optional[List[Dict]],
    google_sheet_rows: List[Dict[str, Any]],
    plan_auto_run: bool = False,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Full S01 bootstrap → intent pre → plan build → intent post.

    PLAN_SCOPE and planning_kernel own scope and catalog connector binding.
    """
    from services.intent.connector_enrichment import apply_pre_planner_connector_hooks
    from services.planning.connector_guards import sanitize_classification_connectors
    from services.planning.conversation_session import (
        inherit_session_resources_from_intent_state,
        latest_extraction_completed_metadata,
    )
    from services.planning.orchestrator import (
        apply_planner_post_classification,
        enrich_classification_with_planning,
    )

    from services.intent.apply import apply_intent_pipeline_to_classification

    from services.planning.planning_activity import emit_planning_activity

    emit_planning_activity("classify_intent", "Classifying intent", "running", segment="scope")

    out = sanitize_classification_connectors(classification_result)
    out["plan_route"] = "fresh_path"
    logger.info("[classify_and_enrich] fresh_path")
    pre_scope_intent = (classification_result.get("intent") or "").strip().lower()
    out = inherit_session_resources_from_intent_state(
        out,
        intent_state_for_classifier,
        conversation_history=conversation_history,
        prompt=prompt,
    )
    from services.planning.plan_execution_gates import merge_execution_state_into_classification

    out = merge_execution_state_into_classification(
        out,
        conversation_history=conversation_history,
    )
    out = await apply_intent_pipeline_to_classification(
        out,
        prompt=prompt,
        user_id=str(user_id),
        catalog=resource_catalog,
        conversation_history=conversation_history,
        intent_state_for_classifier=intent_state_for_classifier,
        conversation_id=str(conversation_id) if conversation_id else None,
        phase="pre_planning",
    )
    out = apply_planner_post_classification(
        out,
        prompt=prompt,
        catalog=resource_catalog,
        conversation_history=conversation_history,
        extraction_meta=latest_extraction_completed_metadata(conversation_history),
    )
    out = apply_pre_planner_connector_hooks(
        out,
        prompt=prompt,
        conversation_history=conversation_history,
        google_sheet_rows=google_sheet_rows,
        intent_state_for_classifier=intent_state_for_classifier,
    )

    out = await enrich_classification_with_planning(
        out,
        user_id=str(user_id),
        prompt=prompt,
        catalog=resource_catalog,
        plan_auto_run=plan_auto_run,
        conversation_history=conversation_history,
        conversation_id=str(conversation_id) if conversation_id else None,
    )

    out = await apply_intent_pipeline_to_classification(
        out,
        prompt=prompt,
        user_id=str(user_id),
        catalog=resource_catalog,
        conversation_history=conversation_history,
        intent_state_for_classifier=intent_state_for_classifier,
        conversation_id=str(conversation_id) if conversation_id else None,
        phase="post_planning",
    )

    from services.intent.metrics import detect_wrong_route
    from services.planning.binding.observability import log_binding

    wrong_route = detect_wrong_route(out, pre_intent=pre_scope_intent)
    pc = out.get("plan_contract") if isinstance(out.get("plan_contract"), dict) else {}
    src = pc.get("source") if isinstance(pc.get("source"), dict) else {}
    clar = [
        c.get("clarification_type")
        for c in (out.get("clarifications") or [])
        if isinstance(c, dict)
    ]
    log_binding(
        "enrich_done",
        f"intent={out.get('intent')} src_entity={src.get('entity_key') or '-'} "
        f"plan={out.get('plan_status')} clar={clar or 'none'} wrong_route={wrong_route}",
    )
    log_planner_event(
        "classification_enriched",
        user_id=str(user_id),
        plan_id=out.get("plan_id"),
        plan_status=out.get("plan_status"),
        pipeline_state=out.get("pipeline_state"),
        clarification_types=clar or None,
        level=logging.INFO,
    )
    emit_planning_activity("classify_intent", "Classifying intent", "done", segment="scope")
    emit_planning_activity("parse_prompt", "Understanding your request", "done", segment="scope")
    out = _strip_plan_if_conversation_turn(out, prompt)
    return out


def _strip_plan_if_conversation_turn(
    classification: Dict[str, Any],
    prompt: Optional[str],
) -> Dict[str, Any]:
    """Safety net: Gate 0 info/general turns must never retain an execution plan."""
    del prompt
    out = dict(classification or {})
    plan_route = (out.get("plan_route") or "").strip()
    if plan_route not in ("info_path", "general_path"):
        return out
    from services.intent.project import _strip_conversation_plan_fields

    out["mode"] = "general"
    _strip_conversation_plan_fields(out)
    return out
