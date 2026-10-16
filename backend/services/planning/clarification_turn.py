"""Resolve free-text architect turns: clarification resume, Gate 0, classify_and_enrich."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def is_explicit_planner_ui_resume(planner_resume: Optional[Dict[str, Any]]) -> bool:
    """
    True when the client sent an explicit UI action (button click / Run plan).

    reply_text-only payloads are NOT explicit — they must go through Gate 0 + normal routing.
    """
    if not planner_resume or not isinstance(planner_resume, dict):
        return False
    if planner_resume.get("selections"):
        return True
    for flag in (
        "approve_plan",
        "approve_schema_deploy",
        "skip_schema_deploy",
        "approve_recovery",
        "skip_recovery",
        "resume_plan_execution",
    ):
        if planner_resume.get(flag):
            return True
    return False


async def _clarification_reply_allowed(
    text: str,
    *,
    user_id: str,
    conversation_history: Optional[List[Dict[str, Any]]],
    conversation_id: Optional[str],
    original: str,
    open_clarifications: List[Dict[str, Any]],
    gate0_pipeline: bool,
) -> bool:
    """True when the message may continue an open clarification thread."""
    from services.planning.clarification_reply_routing import resolve_clarification_reply_routing

    if not open_clarifications:
        return False

    decision = await resolve_clarification_reply_routing(
        text,
        user_id=str(user_id),
        conversation_history=conversation_history,
        conversation_id=conversation_id,
        original_prompt=original,
        open_clarifications=open_clarifications,
    )
    return decision.is_clarification_reply


async def try_resume_clarification_from_prompt(
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]],
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    plan_auto_run: bool = False,
    gate0_pipeline: bool = False,
    skip_reply_routing: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    When an open planner clarification exists in chat history, map free-text to selections
    and resume planning. Returns None when the prompt is not a clarification answer.
    """
    from services.planning.clarification_reply import parse_clarification_reply
    from services.planning.orchestrator import resume_classification_after_clarifications
    from services.planning.plan_context import (
        find_extract_step_pending,
        latest_clarification_pending_from_history,
        latest_stalled_planner_pending_from_history,
    )
    from services.planning.plan_execution import apply_extract_step_resume

    text = (prompt or "").strip()
    if not text:
        return None

    extract_pending = find_extract_step_pending(conversation_history)
    if extract_pending and skip_reply_routing:
        logger.info(
            "[clarification_turn] extract_step_resume conversation_id=%s",
            conversation_id,
        )
        return apply_extract_step_resume(extract_pending, text)

    pending = latest_clarification_pending_from_history(conversation_history)
    if not pending:
        pending = latest_stalled_planner_pending_from_history(conversation_history)
    if not pending:
        return None

    open_clarifications = (
        pending.get("clarifications")
        or (pending.get("classification") or {}).get("clarifications")
        or []
    )

    if not open_clarifications:
        ep = pending.get("execution_plan") or {}
        steps = (ep.get("steps") or []) if isinstance(ep, dict) else []
        if not steps and not pending.get("plan_execution_pending"):
            logger.info(
                "[clarification_turn] reassembly_from_pending conversation_id=%s",
                conversation_id,
            )
            return await resume_classification_after_clarifications(
                pending,
                [],
                user_id=str(user_id),
                prompt=(pending.get("original_prompt") or "").strip() or None,
                plan_auto_run=plan_auto_run,
                conversation_history=conversation_history,
            )
        return None

    original = (pending.get("original_prompt") or "").strip()

    if not skip_reply_routing and not await _clarification_reply_allowed(
        text,
        user_id=str(user_id),
        conversation_history=conversation_history,
        conversation_id=conversation_id,
        original=original,
        open_clarifications=open_clarifications,
        gate0_pipeline=gate0_pipeline,
    ):
        return None

    selections = parse_clarification_reply(text, open_clarifications)
    if not selections:
        from services.planning.binding.clarification_resolve import (
            resolve_clarification_selections_llm,
        )
        from services.planning.plan_scope import read_plan_scope

        base = pending.get("classification") or {}
        selections = await resolve_clarification_selections_llm(
            text,
            open_clarifications,
            user_id=str(user_id),
            classification=base,
            prompt=original or None,
            conversation_id=conversation_id or pending.get("conversation_id"),
            parsed_goal=read_plan_scope(base) or None,
            conversation_history=conversation_history,
        )

    if not selections:
        return None

    logger.info(
        "[clarification_turn] resume_from_history conversation_id=%s selection_count=%s",
        conversation_id,
        len(selections),
    )
    return await resume_classification_after_clarifications(
        pending,
        selections,
        user_id=str(user_id),
        prompt=(pending.get("original_prompt") or "").strip() or None,
        reply_text=text,
        plan_auto_run=plan_auto_run,
        conversation_history=conversation_history,
    )


async def classify_architect_turn(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    *,
    conversation_id: Optional[str] = None,
    plan_auto_run: bool = False,
    explicit_source: Optional[str] = None,
    explicit_source_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Single classification entry for architect free-text turns.

    Order: Gate 0 (info/general/pipeline) → classify_and_enrich (pipeline only).

    Planning continuation (clar answer in chat) runs inside classify_and_enrich after
    Gate 0 — not before. See docs/ROUTING_REFACTOR_PLAN.md.
    """
    from handlers.classification_handler import classify_source_destination
    from services.intent.classify_context import load_classify_context
    from services.intent.conversation_turn import enrich_general_turn, enrich_info_turn
    from services.intent.turn_type_routing import resolve_turn_type_routing

    pl = (prompt or "").strip()

    gate0 = await resolve_turn_type_routing(
        pl,
        user_id=str(user_id),
        conversation_id=str(conversation_id) if conversation_id else None,
        conversation_history=conversation_history,
    )
    if gate0.plan_route == "info_path":
        logger.info(
            "[classify_architect_turn] gate0=info_path conf=%.2f",
            gate0.confidence,
        )
        ctx = await load_classify_context(str(user_id))
        return await enrich_info_turn(
            pl,
            user_id=str(user_id),
            catalog=ctx.catalog,
            conversation_id=str(conversation_id) if conversation_id else None,
        )
    if gate0.plan_route == "general_path":
        logger.info(
            "[classify_architect_turn] gate0=general_path conf=%.2f",
            gate0.confidence,
        )
        return await enrich_general_turn(
            pl,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
            conversational_turn=gate0.conversational_turn,
            off_topic=gate0.off_topic,
        )

    return await classify_source_destination(
        pl,
        user_id,
        conversation_history,
        conversation_id=conversation_id,
        explicit_source=explicit_source,
        explicit_source_name=explicit_source_name,
        plan_auto_run=plan_auto_run,
        gate0_decision=gate0,
    )
