"""Approved-plan thread context for follow-up turns (extract clarifications, short replies)."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional

from services.planning.conversation_session import is_unknown_resource

# Events that mean an approved plan / extract clarification thread is finished.
_PIPELINE_COMPLETE_EVENTS = frozenset({"migration_completed"})
_PLAN_PROGRESS_AFTER_PENDING_EVENTS = frozenset(
    {
        "migration_completed",
        "migration_started",
        "extraction_completed",
        "mapping_completed",
        "mapping_started",
    }
)


def merge_execution_prompt(original: str, reply: str) -> str:
    """Combine the user's original goal with a follow-up detail for re-execution."""
    base = (original or "").strip()
    detail = (reply or "").strip()
    if not detail:
        return base
    if not base:
        return detail
    if detail.lower() in base.lower():
        return base
    return f"{base}\n\nAdditional detail: {detail}"


def effective_planning_prompt(
    prompt: str,
    *,
    conversation_history: Optional[List[Dict]] = None,
    pending_original: Optional[str] = None,
) -> str:
    """Thread goal text for PLAN_SCOPE and binding — original plan goal plus this turn."""
    original = (pending_original or "").strip()
    if not original and conversation_history:
        pending = latest_clarification_pending_from_history(conversation_history)
        if pending:
            original = (pending.get("original_prompt") or "").strip()
    current = (prompt or "").strip()
    if original:
        return merge_execution_prompt(original, current)
    return current


def _message_metadata(msg: Dict[str, Any]) -> Dict[str, Any]:
    meta = msg.get("metadata")
    return meta if isinstance(meta, dict) else {}


def _message_is_planner_resolved(msg: Dict[str, Any]) -> bool:
    if msg.get("plannerResolved"):
        return True
    meta = _message_metadata(msg)
    if meta.get("plannerResolved") or meta.get("planner_resolved"):
        return True
    if meta.get("plan_dismissed"):
        return True
    if meta.get("event_type") == "plan_dismissed":
        return True
    return False


def conversation_has_completed_pipeline(
    conversation_history: Optional[List[Dict]],
) -> bool:
    """True when migration finished — new user turns should start fresh."""
    for msg in conversation_history or []:
        if _message_metadata(msg).get("event_type") in _PIPELINE_COMPLETE_EVENTS:
            return True
    return False


def _plan_thread_superseded(
    conversation_history: Optional[List[Dict]],
    pending_index: int,
) -> bool:
    """Pending plan/clarification is stale if the pipeline progressed after it."""
    if pending_index < 0:
        return False
    for msg in (conversation_history or [])[pending_index + 1 :]:
        if _message_metadata(msg).get("event_type") in _PLAN_PROGRESS_AFTER_PENDING_EVENTS:
            return True
    return False


def _latest_pending_from_history(
    conversation_history: Optional[List[Dict]],
    *,
    predicate: Callable[..., bool],
) -> Optional[Dict[str, Any]]:
    history = conversation_history or []
    for idx in range(len(history) - 1, -1, -1):
        msg = history[idx]
        if _message_is_planner_resolved(msg):
            continue
        meta = _message_metadata(msg)
        pending = meta.get("planner_pending")
        if not isinstance(pending, dict):
            continue
        if not predicate(msg=msg, pending=pending, meta=meta):
            continue
        if _plan_thread_superseded(history, idx):
            return None
        return pending
    return None


def format_open_clarification_summary(clarifications: List[Dict[str, Any]]) -> str:
    """Structured open-gap summary for Gate 0 and pipeline-turn routing LLMs."""
    lines: List[str] = []
    for clar in clarifications or []:
        if not isinstance(clar, dict):
            continue
        ctype = (clar.get("clarification_type") or "").strip()
        prompt = (clar.get("prompt") or "").strip()
        if ctype:
            lines.append(f"- {ctype}: {prompt[:200] if prompt else '(no prompt)'}")
    return "\n".join(lines) if lines else "(none)"


def open_clarifications_for_routing(
    conversation_history: Optional[List[Dict]],
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Latest unresolved planner pending and its open clarification gaps."""
    pending = latest_clarification_pending_from_history(conversation_history)
    if not pending:
        pending = latest_stalled_planner_pending_from_history(conversation_history)
    if not pending:
        return None, []
    open_clarifications = (
        pending.get("clarifications")
        or (pending.get("classification") or {}).get("clarifications")
        or []
    )
    return pending, open_clarifications


def build_open_clarification_routing_block(
    conversation_history: Optional[List[Dict]],
) -> str:
    """Prompt section for routing LLMs when a planner clarification gap is open."""
    pending, open_clarifications = open_clarifications_for_routing(conversation_history)
    if not open_clarifications:
        return ""
    original = ((pending or {}).get("original_prompt") or "").strip()
    gap_block = format_open_clarification_summary(open_clarifications)
    return (
        f"\n**Open planner clarification gap(s):**\n{gap_block}\n\n"
        f"**Original goal when clarification started:**\n"
        f"\"\"\"{original or '(unknown)'}\"\"\"\n"
    )


def latest_plan_thread_pending(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Most recent unresolved planner pending from an approved or executing plan thread."""

    def _match(*, msg: Dict[str, Any], pending: Dict[str, Any], meta: Dict[str, Any], **_kw):
        if pending.get("extract_step_clarification") and pending.get("plan_execution_pending"):
            return True
        cls = pending.get("classification") or {}
        plan_status = (cls.get("plan_status") or meta.get("plan_status") or "").lower()
        return meta.get("event_type") == "planner_plan_ready" and plan_status in (
            "approved",
            "running",
            "awaiting_approval",
        )

    return _latest_pending_from_history(conversation_history, predicate=_match)


def find_extract_step_pending(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    def _match(*, pending: Dict[str, Any], **_kw):
        return bool(pending.get("extract_step_clarification"))

    return _latest_pending_from_history(conversation_history, predicate=_match)


def latest_stalled_planner_pending_from_history(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    """
    Planner pending with no execution steps yet — e.g. UI picks applied but assembly
    did not finish. Used to re-run finalize instead of treating chat as a new turn.
    """

    def _match(*, pending: Dict[str, Any], meta: Dict[str, Any], **_kw):
        if pending.get("plan_execution_pending") or pending.get("extract_step_clarification"):
            return False
        event_type = meta.get("event_type") or ""
        if event_type not in ("planner_clarification_required", "planner_plan_ready"):
            return False
        ep = pending.get("execution_plan") or {}
        steps = ep.get("steps") if isinstance(ep, dict) else None
        if steps:
            return False
        cls = pending.get("classification") or {}
        plan_status = (cls.get("plan_status") or meta.get("plan_status") or "").lower()
        if plan_status in ("approved", "running"):
            return False
        return bool((pending.get("original_prompt") or "").strip())

    return _latest_pending_from_history(conversation_history, predicate=_match)


def latest_clarification_pending_from_history(
    conversation_history: Optional[List[Dict]],
) -> Optional[Dict[str, Any]]:
    """Most recent unresolved pre-approval planner clarification from chat metadata."""

    def _match(*, pending: Dict[str, Any], meta: Dict[str, Any], **_kw):
        event_type = meta.get("event_type") or ""
        if event_type == "planner_plan_execution_clarification":
            return bool(pending.get("extract_step_clarification"))
        if event_type != "planner_clarification_required":
            return False
        clarifications = pending.get("clarifications") or []
        cls = pending.get("classification") or {}
        if not clarifications:
            clarifications = cls.get("clarifications") or []
        plan_status = (cls.get("plan_status") or meta.get("plan_status") or "").lower()
        if plan_status == "awaiting_approval":
            return False
        return bool(clarifications) or plan_status == "awaiting_clarification"

    return _latest_pending_from_history(conversation_history, predicate=_match)


_PLAN_EXECUTION_STATE_KEYS = (
    "_plan_completed_step_ids",
    "plan_resume_step_index",
    "active_job_id",
    "mapping_id",
    "mapping_config",
    "destination_spreadsheet_id",
    "operations_applied",
    "extraction_version",
)


def latest_plan_execution_state_from_history(
    conversation_history: Optional[List[Dict]],
) -> Dict[str, Any]:
    """
    Rebuild execution progress from the latest plan pause or step checkpoint.

    Used when a new HTTP request resumes an approved plan without an explicit
    planner_resume payload from the client.
    """
    history = conversation_history or []
    best: Dict[str, Any] = {}

    for msg in reversed(history):
        meta = _message_metadata(msg)
        pending = meta.get("planner_pending")
        if isinstance(pending, dict):
            cls = pending.get("classification")
            if isinstance(cls, dict):
                candidate = {k: cls[k] for k in _PLAN_EXECUTION_STATE_KEYS if cls.get(k) not in (None, "", [])}
                if candidate.get("_plan_completed_step_ids") or candidate.get("plan_resume_step_index") is not None:
                    return candidate
                if candidate:
                    best = {**best, **candidate}

        if meta.get("event_type") == "plan_step_checkpoint":
            detail = meta.get("detail") if isinstance(meta.get("detail"), dict) else {}
            completed = detail.get("_plan_completed_step_ids")
            if completed:
                best["_plan_completed_step_ids"] = completed
            for key in (
                "active_job_id",
                "destination_spreadsheet_id",
                "mapping_id",
                "mapping_config",
                "operations_applied",
                "extraction_version",
            ):
                if detail.get(key) and not best.get(key):
                    best[key] = detail[key]
            if best.get("_plan_completed_step_ids"):
                return best

    return best


def apply_active_plan_thread_context(
    classification: Dict[str, Any],
    prompt: Optional[str],
    conversation_history: Optional[List[Dict]],
) -> Dict[str, Any]:
    """
    Carry source/destination (and related fields) from the active approved plan thread
    when continuation routing owns the turn.
    """
    del prompt
    out = dict(classification or {})
    if conversation_has_completed_pipeline(conversation_history):
        return out

    sa = (out.get("session_action") or "").strip()
    route = (out.get("plan_route") or "").strip()
    if route != "continuation_path" and sa != "continue_active_job":
        return out

    pending = find_extract_step_pending(conversation_history)
    if not pending:
        return out

    original = (pending.get("original_prompt") or "").strip()
    plan_cls = dict(pending.get("classification") or {})
    for key in (
        "source",
        "destination",
        "source_name",
        "destination_name",
        "destination_same_as_source",
        "source_sheet_name",
        "destination_sheet_name",
        "destination_spreadsheet_id",
        "execution_plan",
        "plan_id",
        "plan_status",
        "plan_steps_summary",
        "_plan_completed_step_ids",
        "plan_resume_step_index",
        "active_job_id",
        "mapping_id",
    ):
        cur = out.get(key)
        if (cur is None or is_unknown_resource(str(cur) if isinstance(cur, str) else None)) and plan_cls.get(
            key
        ) is not None:
            out[key] = plan_cls[key]

    if plan_cls.get("plan_status") == "approved":
        out["plan_status"] = "approved"
        out["needs_clarification"] = False

    if original:
        out["plan_thread_original_prompt"] = original
    out["plan_thread_active"] = True
    return out


# --- Session context (merged P4.9) ---

"""Session and pipeline state helpers for planner v2."""

from typing import Any, Dict, List, Optional

from services.planning.conversation_session import latest_extraction_session_for_classifier


async def load_session_context(
    *,
    user_id: str,
    conversation_id: Optional[str],
    conversation_history: Optional[List[Dict]],
    source_type: Optional[str] = None,
    current_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """Unified session view for planner (intent state + latest extraction)."""
    intent_state = None
    if conversation_id or conversation_history:
        from services.intent.session_state import get_active_intent_state

        intent_state = await get_active_intent_state(
            conversation_id,
            user_id,
            conversation_history,
            source_type=source_type,
            current_prompt=current_prompt,
        )
    latest = latest_extraction_session_for_classifier(conversation_history)
    return {
        "intent_state": intent_state,
        "latest_extraction": latest,
    }


def pipeline_state_from_plan(plan_status: str, *, needs_clarification: bool = False) -> str:
    if needs_clarification:
        return "awaiting_clarification"
    return plan_status or "draft"

