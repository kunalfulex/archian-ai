"""Continuation-path planning: job facts + S01 decision → minimal plan."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from services.intent.job_context import JobContext
from services.intent.session_continuation import SessionContinuationDecision
from services.planning.plan_builder import build_plan_steps, finalize_plan_status
from services.planning.plan_context import pipeline_state_from_plan
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, merge_plan_into_classification

logger = logging.getLogger(__name__)

_MAP_DEST_CONTINUATION_STEPS = frozenset({"map", "migrate"})


def _build_continuation_plan_contract_from_job(job: JobContext) -> Dict[str, Any]:
    """Minimal resolved plan_contract from job facts (continuation skips fresh bind)."""
    from services.planning.schemas import PlanContract, PlanEntityRef

    entity = (job.source.entity_key or "").strip()
    src = PlanEntityRef(
        connector_type=job.source.connector_type,
        connector_id=job.source.connector_id,
        display_name=job.source.display_name,
        entity_key=entity or None,
        entity_label=entity or None,
        catalog_label=entity or None,
        resolution_source="plan_pinned",
        confidence=0.95 if entity else 0.85,
    )
    dest = PlanEntityRef(connector_type="unknown")
    if job.destination:
        dest_entity = job.destination.entity_key or job.destination_object_api_name
        dest = PlanEntityRef(
            connector_type=job.destination.connector_type,
            connector_id=job.destination.connector_id,
            display_name=job.destination.display_name,
            entity_key=dest_entity or None,
            entity_label=dest_entity or None,
            catalog_label=dest_entity or None,
            resolution_source="plan_pinned",
            confidence=0.9 if dest_entity else 0.85,
        )
    migration_intent: Optional[Dict[str, Any]] = None
    if job.field_hints or job.include_related_data:
        migration_intent = {
            "driving_entity_key": entity or None,
            "driving_entity_label": entity or None,
            "field_hints": list(job.field_hints),
            "extract_field_scope": job.extract_field_scope,
            "needs_extract_clarification": False,
        }
        if job.include_related_data:
            migration_intent["include_related_data"] = True
            if job.parent_entity_key:
                migration_intent["parent_entity_key"] = job.parent_entity_key
                migration_intent["related_entity_key"] = job.parent_entity_key
            if job.parent_entity_label:
                migration_intent["parent_entity_label"] = job.parent_entity_label
                migration_intent["related_entity_label"] = job.parent_entity_label
            if job.related_field_hints:
                migration_intent["related_field_hints"] = list(job.related_field_hints)
            if job.related_entity_keys:
                migration_intent["related_entity_keys"] = list(job.related_entity_keys)
            if job.lookup_field_path:
                migration_intent["lookup_field_path"] = job.lookup_field_path
            if job.lookup_relationship_name:
                migration_intent["lookup_relationship_name"] = job.lookup_relationship_name
    contract = PlanContract(source=src, destination=dest, migration_intent=migration_intent)
    return contract.model_dump(mode="json")


def _base_classification_from_job(
    decision: SessionContinuationDecision,
    job: JobContext,
) -> Dict[str, Any]:
    """Build classification pins from job facts (no goal / continuation steps yet)."""
    cls: Dict[str, Any] = {
        "session_action": decision.session_action,
        "continuation_phase": decision.continuation_phase,
        "session_confidence": decision.confidence,
        "active_job_id": job.job_id,
        "_session_continuation": decision.model_dump(mode="json"),
        "_session_progress": job.s01_progress_dict(),
        "last_completed_step": job.last_completed_step,
        "source": job.source.connector_type,
        "source_name": job.source.display_name,
        "extract_fields_confirmed": True,
        "planner_source_entity_confirmed": True,
        "planner_source_confirmed": True,
    }

    if job.source.connector_id:
        cls["source_connector_id"] = job.source.connector_id

    entity = job.source.entity_key or ""
    st = job.source.connector_type
    if st == "salesforce" and entity:
        cls["source_object_api_name"] = entity
        cls["source_entity_key"] = entity
    elif st == "sql" and entity:
        cls["source_table_name"] = entity
        cls["source_entity_key"] = entity
    elif st in ("google_sheets", "gsheets") and entity:
        cls["source_sheet_name"] = entity
        cls["source_entity_key"] = entity
    elif st == "file":
        doc_type = entity or job.destination_object_api_name
        if doc_type:
            cls["document_type"] = doc_type
            cls["source_entity_key"] = doc_type

    plan_contract = _build_continuation_plan_contract_from_job(job)
    if plan_contract.get("source", {}).get("entity_key") or plan_contract.get("source", {}).get("connector_type") not in (
        "unknown",
        "",
        None,
    ):
        cls["plan_contract"] = plan_contract

    if job.field_hints or job.include_related_data:
        migration_intent: Dict[str, Any] = {
            "driving_entity_key": entity,
            "driving_entity_label": entity,
            "field_hints": job.field_hints,
            "extract_field_scope": job.extract_field_scope,
            "needs_extract_clarification": False,
        }
        if job.include_related_data:
            migration_intent["include_related_data"] = True
            if job.parent_entity_key:
                migration_intent["parent_entity_key"] = job.parent_entity_key
                migration_intent["related_entity_key"] = job.parent_entity_key
            if job.parent_entity_label:
                migration_intent["parent_entity_label"] = job.parent_entity_label
                migration_intent["related_entity_label"] = job.parent_entity_label
            if job.related_field_hints:
                migration_intent["related_field_hints"] = list(job.related_field_hints)
            if job.related_entity_keys:
                migration_intent["related_entity_keys"] = list(job.related_entity_keys)
            if job.lookup_field_path:
                migration_intent["lookup_field_path"] = job.lookup_field_path
            if job.lookup_relationship_name:
                migration_intent["lookup_relationship_name"] = job.lookup_relationship_name
        cls["migration_intent"] = migration_intent

    cls["reasoning"] = decision.reasoning_short or f"Continue on job {job.job_id}"
    return cls


def classification_from_job_context(
    decision: SessionContinuationDecision,
    job: JobContext,
    *,
    prompt: Optional[str] = None,
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build legacy classification dict from job pins + optional enriched goal.

    When goal is omitted, applies a minimal S01-phase fallback (sync tests only).
    Production path uses plan_from_job_context which calls PLAN_SCOPE first.
    """
    from services.planning.continuation_policy import (
        apply_continuation_goal_to_classification,
    )
    from services.planning.goal_parser import parse_goal

    cls = _base_classification_from_job(decision, job)

    if goal is not None:
        return apply_continuation_goal_to_classification(cls, goal, job, decision)

    parsed = parse_goal(cls, prompt)
    cls["_plan_scope"] = parsed
    return cls


async def plan_from_job_context(
    *,
    user_id: str,
    prompt: str,
    session_decision: SessionContinuationDecision,
    job: JobContext,
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    plan_auto_run: bool = False,
) -> Dict[str, Any]:
    """
    Continuation path: S01 + job facts + PLAN_SCOPE → multi-step plan when requested.
    """
    from services.planning.continuation_policy import (
        CONTINUATION_DEST_INHERIT,
        CONTINUATION_DEST_RESOLVE,
        apply_continuation_endpoint_pins_from_job,
        apply_continuation_goal_to_classification,
        apply_job_destination_pins,
        reconcile_continuation_scope_after_destination,
        resolve_continuation_destination_mode,
    )
    from services.planning.goal_parser import enrich_continuation_scope_from_llm
    from services.planning.observability import log_planner_event

    cls = _base_classification_from_job(session_decision, job)
    cls["plan_route"] = "continuation_path"
    cls["original_prompt"] = (prompt or "").strip() or None

    from services.intent.job_authority import apply_job_authority

    cls = apply_job_authority(cls, job)

    goal = await enrich_continuation_scope_from_llm(
        cls,
        prompt,
        user_id=str(user_id),
        job=job,
        conversation_id=conversation_id,
        conversation_history=conversation_history,
        session_decision=session_decision,
    )

    from services.planning.continuation_session_enrich import (
        enrich_continuation_goal_from_session_facts,
    )

    cls["_plan_scope"] = goal
    cls, goal = await enrich_continuation_goal_from_session_facts(
        cls,
        goal,
        user_id=str(user_id),
        prompt=prompt or "",
        conversation_history=conversation_history,
        conversation_id=conversation_id,
        job_id=job.job_id,
    )

    cls = apply_continuation_goal_to_classification(cls, goal, job, session_decision)
    goal = dict(cls.get("_plan_scope") or goal)

    cont_steps = cls.get("_continuation_steps") or []
    logger.info(
        "[continuation_plan] job=%s llm_ops=%s resolved_steps=%s",
        job.job_id,
        goal.get("operations"),
        cont_steps,
    )

    dest_mode = resolve_continuation_destination_mode(
        cont_steps, goal, job, catalog=catalog
    )
    inherit_job_dest = dest_mode == CONTINUATION_DEST_INHERIT
    needs_connector_resolution = dest_mode == CONTINUATION_DEST_RESOLVE

    if inherit_job_dest:
        cls = apply_job_destination_pins(cls, job)
    elif dest_mode == CONTINUATION_DEST_RESOLVE:
        cls = apply_continuation_endpoint_pins_from_job(cls, goal, job)
        goal = dict(cls.get("_plan_scope") or goal)
        cls["_plan_scope"] = goal

    from services.planning.map_flow_debug import classification_map_pins, log_map_flow

    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        goal_summary=None,
        metadata={
            "classification": dict(cls),
            "goal": goal,
            "continuation": True,
            "job_id": job.job_id,
            "pipeline_stage": job.pipeline_stage,
            "continuation_steps": cont_steps,
            "original_prompt": (prompt or "").strip() or None,
            "session_snapshot": job.s01_progress_dict(),
        },
    )

    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.destination_catalog_validate import read_needs_destination
    from services.planning.planning_kernel import run_planning_kernel
    from services.planning.source_catalog_validate import read_needs_source

    session = PlannerSession(
        classification=cls,
        plan=plan,
        parsed_goal=goal,
        prompt=prompt or "",
        conversation_history=conversation_history,
    )
    session = await run_planning_kernel(
        session,
        plan_route="continuation_path",
        user_id=str(user_id),
        catalog=catalog,
        job=job,
        conversation_id=conversation_id,
        conversation_history=conversation_history,
        plan_auto_run=plan_auto_run,
        skip_destination_resolver=inherit_job_dest or not needs_connector_resolution,
        run_source_resolver=read_needs_source(cls, goal),
        run_destination_resolver=needs_connector_resolution
        and read_needs_destination(cls, goal),
    )
    cls = session.classification
    plan = session.plan
    goal = dict(cls.get("_plan_scope") or goal)

    cls, goal, cont_steps = reconcile_continuation_scope_after_destination(
        cls, goal, job, session_decision
    )
    cls["_plan_scope"] = goal
    dest_mode = resolve_continuation_destination_mode(
        cont_steps, goal, job, catalog=catalog
    )
    inherit_job_dest = dest_mode == CONTINUATION_DEST_INHERIT

    if needs_connector_resolution and cls.get("plan_contract"):
        plan.metadata["plan_contract"] = cls.get("plan_contract")

    log_map_flow(
        "continuation_plan_context",
        job_id=job.job_id,
        prompt_excerpt=(prompt or "")[:200],
        job_destination={
            "connector_type": job.destination.connector_type if job.destination else None,
            "display_name": job.destination.display_name if job.destination else None,
            "entity_key": job.destination.entity_key if job.destination else None,
        },
        prompt_resolved_destination={
            "type": cls.get("destination"),
            "name": cls.get("destination_name"),
            "sheet": cls.get("destination_sheet_name"),
            "entity": cls.get("destination_object_api_name"),
            "has_destination": bool(cls.get("destination_name")),
            "has_entity": bool(cls.get("destination_object_api_name")),
            "resolution": "job_inherit" if inherit_job_dest else "kernel",
        },
        classification_pins=classification_map_pins(cls),
        continuation_steps=cont_steps,
    )

    cls["_plan_scope"] = goal
    cls = apply_job_authority(cls, job)

    if cls.get("plan_contract"):
        plan.metadata["plan_contract"] = cls.get("plan_contract")

    plan = build_plan_steps(cls, prompt=prompt, plan=plan)
    if cls.get("plan_contract"):
        plan.metadata["plan_contract"] = cls.get("plan_contract")
    plan = finalize_plan_status(plan, plan_auto_run=plan_auto_run)
    plan.metadata["continuation"] = True
    plan.metadata["job_id"] = job.job_id
    plan.metadata["original_prompt"] = (prompt or "").strip() or None
    plan.metadata["session_snapshot"] = job.s01_progress_dict()

    out = merge_plan_into_classification(cls, plan)
    out.pop("_plan_scope", None)
    out["plan_route"] = "continuation_path"
    out["original_prompt"] = (prompt or "").strip() or None
    out["pipeline_state"] = pipeline_state_from_plan(
        plan.status,
        needs_clarification=bool(out.get("needs_clarification")),
    )
    from services.planning.plan_extract_gates import assess_planning_state

    artifact = assess_planning_state(
        out,
        plan,
        original_prompt=(prompt or "").strip() or None,
    )
    out["planning_artifact"] = artifact.model_dump(mode="json")

    log_planner_event(
        "continuation_plan_built",
        user_id=str(user_id),
        plan_id=out.get("plan_id"),
        plan_status=out.get("plan_status"),
        pipeline_state=out.get("pipeline_state"),
        extra={
            "job_id": job.job_id,
            "continuation_steps": cont_steps,
            "step_count": len(plan.steps or []),
        },
    )
    logger.info(
        "[continuation_plan] job=%s steps=%s plan_steps=%s",
        job.job_id,
        cont_steps,
        len(plan.steps or []),
    )
    if _MAP_DEST_CONTINUATION_STEPS.intersection(cont_steps):
        from services.planning.map_flow_debug import classification_map_pins, log_map_flow

        log_map_flow(
            "continuation_plan_built_detail",
            job_id=job.job_id,
            plan_id=out.get("plan_id"),
            plan_status=out.get("plan_status"),
            plan_auto_run=plan_auto_run,
            classification_pins=classification_map_pins(out),
            goal_entities=(goal or {}).get("entities"),
            continuation_steps=cont_steps,
        )
    return out
