"""Route architect turns using LLM classification (intent/mode), not phrase lists."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from services.planning.resource_catalog import ResourceCatalog

POST_EXTRACT_INTENTS = frozenset(
    {
        "transform",
        "filter",
        "dedupe",
        "deduplication",
        "validate",
        "validation",
        "multi",
        "cleanup",
    }
)

GENERAL_INTENTS = frozenset({"info", "general"})


def classification_intent_mode(classification: Dict[str, Any]) -> Tuple[str, str]:
    intent = (classification.get("intent") or "").lower()
    mode = (classification.get("mode") or "").lower()
    return intent, mode


def classification_ready_for_agent_execution(classification: Dict[str, Any]) -> bool:
    """
    False when planner still needs clarification or has no executable steps yet.

    Prevents routing to data_ops runtime (e.g. run_data_ops without job_id).
    """
    if classification.get("needs_clarification"):
        return False
    status = (classification.get("plan_status") or "").lower()
    if status in ("awaiting_clarification",):
        return False
    ep = classification.get("execution_plan")
    if not isinstance(ep, dict):
        return True
    steps = ep.get("steps") or []
    ep_status = (ep.get("status") or status or "").lower()
    if not steps and ep_status not in ("approved", "running"):
        return False
    return True


def has_approved_execution_plan(classification: Dict[str, Any]) -> bool:
    ep = classification.get("execution_plan")
    if not isinstance(ep, dict) or not ep.get("steps"):
        return False
    status = (classification.get("plan_status") or ep.get("status") or "").lower()
    return status in ("approved", "running")


def is_map_migrate_classification(classification: Dict[str, Any]) -> bool:
    """True when the classifier chose map or migrate (any natural phrasing)."""
    intent, mode = classification_intent_mode(classification)
    return intent in ("map_data", "migrate_data", "migrate") or mode in ("map", "migrate")


def is_extract_classification(classification: Dict[str, Any]) -> bool:
    intent, mode = classification_intent_mode(classification)
    return intent == "extract" or mode == "extract"


def should_route_to_general_response(
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
) -> bool:
    return classification_is_general_turn(classification, prompt)


def is_migrate_only_classification(classification: Dict[str, Any]) -> bool:
    """Plan should run start_migration without a preceding map_fields step."""
    intent, mode = classification_intent_mode(classification)
    return intent in ("migrate_data", "migrate") or mode == "migrate"


def is_map_only_classification(classification: Dict[str, Any]) -> bool:
    intent, mode = classification_intent_mode(classification)
    return intent == "map_data" or mode == "map"


async def enrich_info_query_classification(
    classification: Dict[str, Any],
    prompt: Optional[str],
    catalog: ResourceCatalog,
    *,
    user_id: str,
) -> Dict[str, Any]:
    """
    Resolve which connected connector the user is asking about before info-tool routing.

    Runs lightweight endpoint + label-aware resolution without building an execution plan.
    """
    out = dict(classification or {})
    intent = (out.get("intent") or "").lower()
    plan_route = (out.get("plan_route") or "").strip()
    if intent != "info" and plan_route != "info_path":
        return out

    out["intent"] = "info"
    out["mode"] = "general"

    source = (out.get("source") or "").lower()
    source_name = (out.get("source_name") or "").strip()
    dest = (out.get("destination") or "").lower()
    dest_name = (out.get("destination_name") or "").strip()

    if source in ("unknown", "") and dest not in ("unknown", ""):
        out["source"] = dest
        if dest_name:
            out["source_name"] = dest_name
        if out.get("destination_connector_id"):
            out["source_connector_id"] = out.get("destination_connector_id")

    if not source_name and dest_name and (out.get("source") or "").lower() == dest:
        out["source_name"] = dest_name

    source = (out.get("source") or "").lower()
    source_name = (out.get("source_name") or "").strip()
    needs_info_bind = source in ("unknown", "") or not source_name or source_name.lower() == "unknown"
    if needs_info_bind:
        from services.planning.connector_llm_resolver import (
            apply_info_connector_pick,
            llm_resolve_info_connector,
        )

        pick = await llm_resolve_info_connector(
            user_id=str(user_id),
            prompt=prompt or "",
            catalog=catalog,
            hint_connector=source if source not in ("unknown", "") else None,
            hint_name=source_name if source_name and source_name.lower() != "unknown" else None,
        )
        if pick:
            out = apply_info_connector_pick(out, pick, catalog)

    connector_type = (out.get("source") or "").lower()
    connector_id = out.get("source_connector_id")
    if connector_type in ("salesforce", "sql", "google_sheets") and connector_id:
        from services.planning.info_entity_enrich import enrich_info_entity_from_labels

        await enrich_info_entity_from_labels(out, prompt or "", str(user_id))

    return out


def classification_is_general_turn(
    classification: dict,
    prompt: Optional[str] = None,
) -> bool:
    """General/info/off-topic — must not run extract or data_ops.

    Trusts Gate 0 ``plan_route`` and classifier fields only.
    """
    del prompt
    from services.planning.planner_path import classification_has_plan_scope

    plan_route = (classification.get("plan_route") or "").strip()
    if plan_route in ("info_path", "general_path"):
        return True

    if classification.get("needs_clarification"):
        return False
    if classification_has_plan_scope(classification):
        return False

    if classification.get("off_topic") or classification.get("scope_violation"):
        return True
    if classification.get("conversational_turn"):
        return True

    intent = (classification.get("intent") or "").lower()
    if intent in ("info", "general"):
        return True
    return False
