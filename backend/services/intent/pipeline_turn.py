"""Pipeline turn helpers — planning continuation and fresh supersede (after Gate 0)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def supersede_pending_for_fresh_turn(
    conversation_history: Optional[List[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """
    When fresh_path wins over an open clarification, return metadata for the superseded pending.

    Frontend may use this to dismiss stale planner panels (Phase 2 SSE).
    """
    from services.planning.plan_context import latest_clarification_pending_from_history
    from services.planning.continuation_policy import should_supersede_pending_for_fresh_turn

    pending = latest_clarification_pending_from_history(conversation_history)
    if not pending:
        return None
    open_clars = (
        pending.get("clarifications")
        or (pending.get("classification") or {}).get("clarifications")
        or []
    )
    if not should_supersede_pending_for_fresh_turn(open_clars):
        logger.info(
            "[pipeline_turn] skip fresh_supersedes — ranked-slot clarification open"
        )
        return None
    original = (pending.get("original_prompt") or "").strip()
    plan_id = (pending.get("classification") or {}).get("plan_id")
    logger.info(
        "[pipeline_turn] fresh_supersedes_pending original=%r plan_id=%s",
        original[:80] if original else "",
        plan_id,
    )
    return {
        "superseded": True,
        "original_prompt": original or None,
        "plan_id": plan_id,
    }


async def continue_planning_from_prompt(
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]],
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    plan_auto_run: bool = False,
    skip_reply_routing: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Planning continuation: map free-text (or implicit reply) to clar selections and resume.

    Runs only after Gate 0 classified the turn as **pipeline** — not before.
    """
    from services.planning.clarification_turn import try_resume_clarification_from_prompt

    return await try_resume_clarification_from_prompt(
        prompt,
        conversation_history,
        user_id=str(user_id),
        conversation_id=conversation_id,
        plan_auto_run=plan_auto_run,
        gate0_pipeline=True,
        skip_reply_routing=skip_reply_routing,
    )


def merge_fresh_supersede_into_classification(
    classification: Dict[str, Any],
    supersede: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach supersede metadata when a fresh turn replaces an open clarification."""
    if not supersede:
        return classification
    out = dict(classification or {})
    out["planner_pending_superseded"] = supersede
    out["_fresh_supersedes_pending"] = True
    return out
