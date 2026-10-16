"""Map PlannerTurnOutcome to SSE events — one event per planner turn, never silent."""
from __future__ import annotations

from typing import Any, AsyncIterator, Dict, List, Optional

from agent.events import (
    planner_clarification_required,
    planner_error,
    planner_plan_ready,
)
from services.planning.planner_session_loop import (
    PlannerTurnKind,
    PlannerTurnOutcome,
    build_planner_turn_outcome,
)


async def emit_planner_turn_outcome(
    outcome: PlannerTurnOutcome,
    *,
    prompt: str,
    conversation_history: List,
    user_id,
    conversation_id: Optional[str],
    route_after_execute=None,
) -> AsyncIterator[str]:
    """
    Yield exactly one primary planner UI event (or delegate to execute route).

    ``route_after_execute`` — async generator from ``_emit_plan_or_route`` execute branch.
    """
    if outcome.kind == PlannerTurnKind.CLARIFICATION:
        yield planner_clarification_required(
            outcome.classification,
            planner_pending=outcome.planner_pending,
        )
        return

    if outcome.kind == PlannerTurnKind.PLAN_READY:
        if outcome.classification.get("execution_plan"):
            yield planner_plan_ready(
                outcome.classification,
                planner_pending=outcome.planner_pending,
            )
        return

    if outcome.kind == PlannerTurnKind.ERROR:
        yield planner_error(
            outcome.message or "",
            classification=outcome.classification,
            planner_pending=outcome.planner_pending,
        )
        return

    if outcome.kind == PlannerTurnKind.EXECUTE and route_after_execute is not None:
        async for chunk in route_after_execute(
            outcome.classification,
            prompt,
            conversation_history,
            user_id,
            conversation_id,
        ):
            yield chunk


def outcome_from_classification(
    classification: Dict[str, Any],
    *,
    prompt: str,
    conversation_id: Optional[str],
    conversation_history: Optional[List] = None,
) -> PlannerTurnOutcome:
    from services.planning.clarification_apply import build_planner_pending_payload

    pending = None
    if conversation_id and (
        classification.get("needs_clarification")
        or not (classification.get("execution_plan") or {}).get("steps")
    ):
        pending = build_planner_pending_payload(
            classification,
            prompt=prompt,
            conversation_id=conversation_id,
            conversation_history=conversation_history,
        )
    return build_planner_turn_outcome(classification, planner_pending=pending)
