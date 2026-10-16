from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def sse_event(event_type: str, payload: Dict[str, Any]) -> str:
    from services.planning.schemas import json_safe_value

    body = {"event_type": event_type, **payload}
    return f"data: {json.dumps(json_safe_value(body))}\n\n"


def classification_started() -> str:
    return sse_event("classification_started", {"status": "running"})


def classification_completed(classification: Dict[str, Any]) -> str:
    """Emit resolved source/destination/intent for operation preview UI."""
    payload: Dict[str, Any] = {
        "status": "done",
        "source": classification.get("source"),
        "destination": classification.get("destination"),
        "intent": classification.get("intent"),
        "mode": classification.get("mode"),
        "source_name": classification.get("source_name"),
        "destination_name": classification.get("destination_name"),
        "source_sheet_name": classification.get("source_sheet_name"),
        "destination_sheet_name": classification.get("destination_sheet_name"),
        "destination_spreadsheet_id": classification.get("destination_spreadsheet_id"),
        "planner_create_spreadsheet": classification.get("planner_create_spreadsheet"),
        "planner_destination_confirmed": classification.get("planner_destination_confirmed"),
        "needs_clarification": bool(classification.get("needs_clarification")),
        "plan_status": classification.get("plan_status"),
    }
    if classification.get("data_operations"):
        payload["data_operations"] = classification.get("data_operations")
    if classification.get("clarifications"):
        payload["clarifications"] = classification.get("clarifications")
    if classification.get("execution_plan"):
        payload["execution_plan"] = classification.get("execution_plan")
    if classification.get("intent_state"):
        payload["intent_state"] = classification.get("intent_state")
    if classification.get("planner_pending_superseded"):
        payload["planner_pending_superseded"] = classification.get("planner_pending_superseded")
    if classification.get("_fresh_supersedes_pending"):
        payload["_fresh_supersedes_pending"] = True
    # Staged chat file — needed if a client ever rebuilds pending from this event.
    if classification.get("_file_upload_pinned"):
        payload["_file_upload_pinned"] = True
    ref = classification.get("_pending_file_ref")
    if isinstance(ref, dict) and (ref.get("file_key") or "").strip():
        payload["_pending_file_ref"] = {
            "file_key": ref.get("file_key"),
            "filename": ref.get("filename"),
            "size_bytes": ref.get("size_bytes"),
        }
    return sse_event("classification_completed", payload)


def planner_plan_ready(
    classification: Dict[str, Any],
    *,
    planner_pending: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
) -> str:
    """Emit approved/draft execution plan for UI preview."""
    plan = classification.get("execution_plan") or {}
    dest = plan.get("destination") if isinstance(plan, dict) else None
    payload: Dict[str, Any] = {
        "status": "plan_ready",
        "plan_id": classification.get("plan_id") or plan.get("plan_id"),
        "plan_status": classification.get("plan_status") or plan.get("status"),
        "goal_summary": plan.get("goal_summary") or classification.get("goal_summary"),
        "destination": classification.get("destination")
        or (dest.get("connector_type") if isinstance(dest, dict) else None),
        "destination_name": classification.get("destination_name")
        or (dest.get("display_name") if isinstance(dest, dict) else None),
        "destination_sheet_name": classification.get("destination_sheet_name")
        or (dest.get("sheet_name") if isinstance(dest, dict) else None),
        "plan_destination": classification.get("plan_destination"),
        "plan_contract": classification.get("plan_contract"),
        "steps": classification.get("plan_steps_summary")
        or [
            {
                "step_type": s.get("step_type"),
                "label": s.get("label"),
                "tool_id": (s.get("parameters") or {}).get("tool_id"),
            }
            for s in (plan.get("steps") or [])
        ],
        "pipeline_label": (plan.get("metadata") or {}).get("pipeline_label"),
        "pipeline_steps": (plan.get("metadata") or {}).get("pipeline_steps"),
    }
    if planner_pending:
        payload["planner_pending"] = planner_pending
    persisted_id = message_id or classification.get("_persisted_message_id")
    if persisted_id:
        payload["message_id"] = persisted_id
    return sse_event("planner_plan_ready", payload)


def planner_recovery_required(
    *,
    planner_pending: Dict[str, Any],
    recovery_proposal: Optional[Dict[str, Any]] = None,
) -> str:
    """Pause after partial/failed migration until user approves recovery writes."""
    proposal = recovery_proposal or planner_pending.get("recovery_proposal") or {}
    payload: Dict[str, Any] = {
        "status": "recovery_required",
        "message": (
            (proposal.get("narrative") or "").strip()
            or "Review migration errors and approve a fix to continue."
        ),
        "planner_pending": planner_pending,
        "recovery_proposal": proposal,
        "show_recovery_approval": bool(proposal.get("requires_write")),
    }
    return sse_event("planner_recovery_required", payload)


def execution_observation(observation: Dict[str, Any]) -> str:
    """Structured post-step observation for partial/failed migrate (debug + reload)."""
    report = observation.get("failure_report") if isinstance(observation, dict) else None
    message = "Step completed."
    if report and isinstance(report, dict):
        rate = float(report.get("success_rate") or 0)
        failed = int(report.get("failed_count") or 0)
        message = (
            f"Migration observation: {int(rate * 100)}% success, {failed} failed record(s)."
        )
    payload: Dict[str, Any] = {
        "status": observation.get("status") if isinstance(observation, dict) else "success",
        "message": message,
        "observation": observation,
    }
    return sse_event("execution_observation", payload)


def planner_schema_deploy_required(
    *,
    planner_pending: Dict[str, Any],
    schema_proposal: Optional[Dict[str, Any]] = None,
) -> str:
    """Pause plan execution until user approves Salesforce schema deploy."""
    payload: Dict[str, Any] = {
        "status": "schema_deploy_required",
        "message": "Review proposed schema changes and approve deploy to continue mapping.",
        "planner_pending": planner_pending,
    }
    if schema_proposal:
        payload["schema_proposal"] = schema_proposal
    return sse_event("planner_schema_deploy_required", payload)


def planner_plan_execution_clarification(
    *,
    message: str,
    planner_pending: Dict[str, Any],
) -> str:
    """Pause approved plan execution for an in-tool extract clarification (text reply)."""
    return sse_event(
        "planner_plan_execution_clarification",
        {
            "status": "clarification",
            "message": (message or "").strip() or "Please provide more detail to continue.",
            "planner_pending": planner_pending,
            "plan_execution_pending": True,
            "extract_step_clarification": True,
        },
    )


def planner_error(
    message: str,
    *,
    classification: Optional[Dict[str, Any]] = None,
    planner_pending: Optional[Dict[str, Any]] = None,
) -> str:
    """Explicit planner failure — never leave the UI silent after a planner turn."""
    payload: Dict[str, Any] = {
        "status": "error",
        "message": (message or "").strip()
        or "Planning could not continue. Please try again.",
    }
    if planner_pending:
        payload["planner_pending"] = planner_pending
    if classification:
        payload["plan_status"] = classification.get("plan_status")
        payload["needs_clarification"] = bool(classification.get("needs_clarification"))
        if classification.get("execution_plan"):
            payload["execution_plan"] = classification.get("execution_plan")
    return sse_event("planner_error", payload)


def planner_clarification_required(
    classification: Dict[str, Any],
    *,
    message: Optional[str] = None,
    planner_pending: Optional[Dict[str, Any]] = None,
) -> str:
    """Blocking clarifications before pipeline execution."""
    clarifications = classification.get("clarifications") or []
    ep = classification.get("execution_plan")
    if isinstance(ep, dict):
        try:
            from services.planning.schemas import ExecutionPlan

            plan = ExecutionPlan.model_validate(ep)
            blocking = plan.blocking_clarifications()
            if blocking:
                clarifications = [c.model_dump(mode="json") for c in blocking]
        except Exception:
            pass
    from services.planning.clarification_compose import resolve_clarification_display_message

    display_message = resolve_clarification_display_message(
        clarifications,
        classification=classification,
        user_prompt=str(classification.get("original_prompt") or ""),
        message=message,
    )

    return sse_event(
        "planner_clarification_required",
        {
            "status": "clarification",
            "message": display_message,
            "needs_clarification": True,
            "clarifications": clarifications,
            "execution_plan": classification.get("execution_plan"),
            "plan_id": classification.get("plan_id")
            or (planner_pending or {}).get("plan_id"),
            "planner_pending": planner_pending,
        },
    )


def planning_activity(
    activity_id: str,
    label: str,
    status: str,
    *,
    detail: Optional[str] = None,
    segment: Optional[str] = None,
) -> str:
    """Append-only planning milestone while scope/plan is being discovered."""
    body: Dict[str, Any] = {
        "activity_id": activity_id,
        "label": label,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    if segment:
        body["segment"] = segment
    return sse_event("planning_activity", body)


def operation_phase(
    phase: str,
    status: str,
    *,
    label: Optional[str] = None,
    progress: Optional[int] = None,
    detail: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> str:
    """User-facing pipeline step (pending | running | done | error)."""
    body: Dict[str, Any] = {"phase": phase, "status": status}
    if label:
        body["label"] = label
    if progress is not None:
        body["progress"] = progress
    if detail:
        body["detail"] = detail
    if meta:
        body["meta"] = meta
    return sse_event("operation_phase", body)


def agent_planning() -> str:
    return sse_event("agent_planning", {"status": "started"})


def agent_plan_ready(
    plan_summary: str,
    *,
    user_label: Optional[str] = None,
    pipeline: Optional[List[Dict[str, str]]] = None,
) -> str:
    payload: Dict[str, Any] = {"summary": plan_summary}
    if user_label:
        payload["user_label"] = user_label
    if pipeline:
        payload["pipeline"] = pipeline
    return sse_event("agent_plan_ready", payload)


def agent_step_started(tool_id: str, label: Optional[str] = None) -> str:
    return sse_event("agent_step_started", {"tool_id": tool_id, "label": label or tool_id})


def agent_step_completed(tool_id: str, result: Dict[str, Any]) -> str:
    return sse_event("agent_step_completed", {"tool_id": tool_id, **result})


def agent_turn_completed(session_snapshot: Dict[str, Any]) -> str:
    return sse_event("agent_turn_completed", session_snapshot)


def assistant_message(text: str, *, complete: bool = True) -> str:
    """Structured assistant prose for general/info chat (frontend transcript source of truth)."""
    return sse_event(
        "assistant_message",
        {"text": text, "complete": bool(complete)},
    )


def agent_operation_started(
    operation_id: str,
    label: Optional[str] = None,
    *,
    phase_id: Optional[str] = None,
) -> str:
    """Per plugin inside post-extract (filter, validate, …) — Cursor-like sub-steps."""
    body: Dict[str, Any] = {
        "operation_id": operation_id,
        "label": label or operation_id,
    }
    if phase_id:
        body["phase_id"] = phase_id
    return sse_event("agent_operation_started", body)


def agent_operation_completed(
    operation_id: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    phase_id: Optional[str] = None,
) -> str:
    body: Dict[str, Any] = {"operation_id": operation_id, **(payload or {})}
    if phase_id:
        body["phase_id"] = phase_id
    return sse_event("agent_operation_completed", body)
