"""Structured logging for the planning pipeline."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from services.planning.schemas import ExecutionPlan

logger = logging.getLogger("edison.planning")


def planner_debug_enabled() -> bool:
    return (os.getenv("PLANNER_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def log_planner_event(
    event: str,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    plan_status: Optional[str] = None,
    pipeline_state: Optional[str] = None,
    pipeline_label: Optional[str] = None,
    pipeline_steps: Optional[list] = None,
    clarification_types: Optional[list] = None,
    resolver_hit: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
    level: int = logging.INFO,
) -> None:
    payload: Dict[str, Any] = {
        "event": event,
        "user_id": user_id,
        "plan_id": plan_id,
        "plan_status": plan_status,
        "pipeline_state": pipeline_state,
        "pipeline_label": pipeline_label,
        "pipeline_steps": pipeline_steps,
        "clarification_types": clarification_types,
        "resolver_hit": resolver_hit,
    }
    if user_id is not None:
        payload["user_id"] = user_id
    if conversation_id is not None:
        payload["conversation_id"] = conversation_id
    if extra:
        payload.update(extra)
    logger.log(level, "[planning] %s", {k: v for k, v in payload.items() if v is not None})


def _blocking_types(plan: Any) -> List[str]:
    if isinstance(plan, ExecutionPlan):
        return [c.clarification_type for c in plan.blocking_clarifications()]
    if isinstance(plan, dict):
        clarifications = plan.get("clarifications") or []
        return [
            c.get("clarification_type")
            for c in clarifications
            if c.get("required", True) and not c.get("selected_option_id")
        ]
    return []


def classification_snapshot(classification: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cls = classification or {}
    return {
        "source": cls.get("source"),
        "source_name": cls.get("source_name"),
        "source_connector_id": cls.get("source_connector_id"),
        "source_table_name": cls.get("source_table_name"),
        "destination": cls.get("destination"),
        "destination_name": cls.get("destination_name"),
        "destination_connector_id": cls.get("destination_connector_id"),
        "destination_object_api_name": cls.get("destination_object_api_name"),
        "intent": cls.get("intent"),
        "operations": cls.get("operations"),
        "_source_endpoint_clarified": cls.get("_source_endpoint_clarified"),
        "_destination_endpoint_clarified": cls.get("_destination_endpoint_clarified"),
        "extract_fields_confirmed": cls.get("extract_fields_confirmed"),
        "planner_source_entity_confirmed": cls.get("planner_source_entity_confirmed"),
        "planner_destination_entity_confirmed": cls.get("planner_destination_entity_confirmed"),
        "needs_clarification": cls.get("needs_clarification"),
        "plan_status": cls.get("plan_status"),
        "needs_extract_clarification": cls.get("needs_extract_clarification"),
    }


def plan_snapshot(plan: Any) -> Dict[str, Any]:
    if isinstance(plan, ExecutionPlan):
        return {
            "plan_id": plan.plan_id,
            "status": plan.status,
            "step_count": len(plan.steps or []),
            "blocking_types": _blocking_types(plan),
            "all_clarification_types": [c.clarification_type for c in plan.clarifications],
        }
    if isinstance(plan, dict):
        steps = plan.get("steps") or []
        return {
            "plan_id": plan.get("plan_id"),
            "status": plan.get("status"),
            "step_count": len(steps),
            "blocking_types": _blocking_types(plan),
        }
    return {}


def log_planner_debug(
    event: str,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
    plan: Any = None,
    selection_types: Optional[List[str]] = None,
    gate: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """High-signal checkpoint for tracing dropped planner UI / missing plan steps."""
    if not planner_debug_enabled():
        return
    payload: Dict[str, Any] = {
        "classification": classification_snapshot(classification),
        "plan": plan_snapshot(plan),
    }
    if selection_types:
        payload["selection_types"] = selection_types
    if gate:
        payload["gate"] = gate
    if extra:
        payload.update(extra)
    log_planner_event(
        event,
        user_id=user_id,
        conversation_id=conversation_id,
        plan_id=(plan_snapshot(plan) or {}).get("plan_id") or (classification or {}).get("plan_id"),
        plan_status=(classification or {}).get("plan_status") or (plan_snapshot(plan) or {}).get("status"),
        clarification_types=_blocking_types(plan) or None,
        extra=payload,
        level=logging.DEBUG,
    )


def log_plan_scope_trace(
    stage: str,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    pipeline_ops: Optional[List[Any]] = None,
    operations: Optional[List[Any]] = None,
    needs_source: Optional[bool] = None,
    needs_destination: Optional[bool] = None,
    source_phrase: Optional[str] = None,
    destination_phrase: Optional[str] = None,
    reason: Optional[str] = None,
) -> None:
    """One-line PLAN_SCOPE checkpoint (always INFO — unlike log_planner_debug)."""
    ops = operations if operations is not None else pipeline_ops
    logger.info(
        "[plan_scope] stage=%s ops=%s needs_source=%s needs_dest=%s "
        "source_phrase=%r dest_phrase=%r%s",
        stage,
        list(ops or []),
        needs_source,
        needs_destination,
        ((source_phrase or "")[:80] or None),
        ((destination_phrase or "")[:80] or None),
        f" reason={reason}" if reason else "",
        extra={
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )


def log_plan_empty_dead_end(
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
    plan_id: Optional[str] = None,
    pipeline_tokens: Optional[List[str]] = None,
    goal_ops: Optional[List[str]] = None,
    gap_count: int = 0,
    step_count: int = 0,
    reason: str,
) -> None:
    logger.warning(
        "[plan_scope] empty_plan plan_id=%s tokens=%s goal_ops=%s gaps=%s steps=%s — %s",
        plan_id,
        list(pipeline_tokens or []),
        list(goal_ops or []),
        gap_count,
        step_count,
        reason,
        extra={"conversation_id": conversation_id, "user_id": user_id},
    )
