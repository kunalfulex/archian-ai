"""Mid-plan resume and schema-deploy approval."""
from __future__ import annotations

from typing import Any, Dict, Optional


def build_plan_execution_pending(
    *,
    classification: Dict[str, Any],
    original_prompt: str,
    resume_step_index: int,
    schema_proposal: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Serializable state when execution pauses for schema deploy approval."""
    from services.planning.clarification_apply import build_planner_pending_payload

    pending = build_planner_pending_payload(
        classification,
        prompt=original_prompt,
        conversation_id=conversation_id,
    )
    pending["plan_execution_pending"] = True
    pending["resume_step_index"] = resume_step_index
    if schema_proposal:
        pending["schema_proposal"] = schema_proposal
    return pending


def build_extract_step_pending(
    *,
    classification: Dict[str, Any],
    original_prompt: str,
    resume_step_index: int,
    clarification_message: str,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Pause an approved plan mid-extract until the user answers a tool clarification."""
    pending = build_plan_execution_pending(
        classification=classification,
        original_prompt=original_prompt,
        resume_step_index=resume_step_index,
        conversation_id=conversation_id,
    )
    pending["extract_step_clarification"] = True
    pending["clarification_message"] = (clarification_message or "").strip()
    return pending


def apply_extract_step_resume(
    pending: Dict[str, Any],
    reply_text: Optional[str],
) -> Dict[str, Any]:
    """Re-open an approved plan with merged prompt for the paused extract step."""
    from services.planning.plan_context import merge_execution_prompt
    from services.planning.plan_context import pipeline_state_from_plan

    base = dict(pending.get("classification") or {})
    original = (pending.get("original_prompt") or "").strip()
    reply = (reply_text or "").strip()
    merged = merge_execution_prompt(original, reply)
    base["plan_resume_step_index"] = int(pending.get("resume_step_index") or 0)
    base["plan_status"] = "approved"
    base["needs_clarification"] = False
    base["pipeline_state"] = pipeline_state_from_plan("approved")
    base["_merged_execution_prompt"] = merged
    return base


def apply_schema_deploy_approval(
    pending: Dict[str, Any],
    *,
    skip_deploy: bool = False,
) -> Dict[str, Any]:
    """Mark schema deploy approved and prepare classification to resume execution."""
    base = dict(pending.get("classification") or {})
    base["schema_deploy_approved"] = True
    base["plan_resume_step_index"] = int(pending.get("resume_step_index") or 0)
    base["plan_status"] = "approved"
    base["needs_clarification"] = False
    if skip_deploy:
        base["schema_gate_missing_fields"] = []
    return base


def build_recovery_pending(
    *,
    classification: Dict[str, Any],
    original_prompt: str,
    recovery_proposal: Dict[str, Any],
    batch_id: str,
    job_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Serializable state when execution pauses for migration recovery approval."""
    from services.planning.clarification_apply import build_planner_pending_payload

    pending = build_planner_pending_payload(
        classification,
        prompt=original_prompt,
        conversation_id=conversation_id,
    )
    pending["recovery_pending"] = True
    pending["recovery_proposal"] = recovery_proposal
    pending["recovery_batch_id"] = batch_id
    if job_id:
        pending["recovery_job_id"] = job_id
    return pending


def apply_recovery_approval(
    pending: Dict[str, Any],
    *,
    skip_recovery: bool = False,
) -> Dict[str, Any]:
    """Prepare classification to run recovery mini-plan or dismiss."""
    base = dict(pending.get("classification") or {})
    proposal = dict(pending.get("recovery_proposal") or {})
    base["recovery_approved"] = not skip_recovery
    base["recovery_dismissed"] = skip_recovery
    base["needs_clarification"] = False
    base["plan_status"] = "approved"
    base["recovery_batch_id"] = pending.get("recovery_batch_id")
    base["recovery_job_id"] = pending.get("recovery_job_id")
    base["recovery_iteration_count"] = int(base.get("recovery_iteration_count") or 0) + (
        0 if skip_recovery else 1
    )
    if skip_recovery:
        base["pipeline_complete"] = True
        base["plan_status"] = "completed"
        base.pop("execution_plan", None)
        base.pop("recovery_execution_plan", None)
        return base

    exec_plan = proposal.get("recovery_execution_plan") or {}
    if exec_plan.get("steps"):
        base["recovery_execution_plan"] = exec_plan
        base["execution_plan"] = exec_plan
    decision = proposal.get("decision") or {}
    if decision.get("arguments", {}).get("mapping_context_summary"):
        base["recovery_mapping_context"] = decision["arguments"]["mapping_context_summary"]

    classification = proposal.get("recovery_classification") or {}
    indices = classification.get("recoverable_record_indices") or []
    if indices:
        base["recoverable_record_indices"] = list(indices)
        base["recovery_record_ordinals"] = list(indices)
    if classification.get("recoverable_count") is not None:
        base["recovery_recoverable_count"] = int(classification.get("recoverable_count") or 0)
    if pending.get("recovery_batch_id"):
        base["recovery_parent_batch_id"] = pending.get("recovery_batch_id")
    if pending.get("recovery_job_id"):
        base["recovery_source_job_id"] = pending.get("recovery_job_id") or base.get("active_job_id")
    return base


def is_plan_execution_resume(planner_resume: Optional[Dict[str, Any]]) -> bool:
    if not planner_resume:
        return False
    return bool(
        planner_resume.get("approve_schema_deploy")
        or planner_resume.get("approve_recovery")
        or planner_resume.get("skip_recovery")
        or planner_resume.get("plan_execution_pending")
        or (planner_resume.get("planner_pending") or {}).get("plan_execution_pending")
        or (planner_resume.get("planner_pending") or {}).get("recovery_pending")
    )
