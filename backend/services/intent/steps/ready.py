"""S12 — route and readiness."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.pipeline_routing import pipeline_routing_active
from services.intent.readiness import can_build_plan, destination_blocks_plan
from services.intent.state import IntentState
from services.intent.types import DestinationNeed, IntentStepId, RouteTarget, StepOutcome, StepResult, TurnType


async def run_step_ready(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    cls = classification or {}
    turn = (state.turn_type or "").lower()

    if state.gaps or state.active_gap:
        state.route = RouteTarget.PLANNER.value
        state.readiness = "need_clarification"
    elif turn == TurnType.INFO.value and not pipeline_routing_active(state, cls, prompt=state.prompt):
        state.route = RouteTarget.INFO_QUERY.value
        state.readiness = "route_general"
    elif (turn in (TurnType.GENERAL.value,) or state.conversational) and not pipeline_routing_active(
        state, cls, prompt=state.prompt
    ):
        state.route = RouteTarget.GENERAL_CHAT.value
        state.readiness = "route_general"
    elif pipeline_routing_active(state, cls, prompt=state.prompt):
        if cls.get("needs_clarification") or state.gaps or state.active_gap:
            state.route = RouteTarget.PLANNER.value
            state.readiness = "need_clarification"
        elif can_build_plan(state, cls, prompt=state.prompt):
            state.route = RouteTarget.PLANNER.value
            state.readiness = "ready_to_plan"
        else:
            state.route = RouteTarget.PLANNER.value
            state.readiness = "pending"
    elif can_build_plan(state, cls, prompt=state.prompt):
        state.route = RouteTarget.PLANNER.value
        state.readiness = "ready_to_plan"
    elif destination_blocks_plan(state):
        state.route = RouteTarget.PLANNER.value
        state.readiness = "need_clarification"
    elif state.needs_destination == DestinationNeed.REQUIRED.value and not state.destination.resolved:
        state.route = RouteTarget.PLANNER.value
        state.readiness = "need_clarification"
    else:
        state.route = RouteTarget.PLANNER.value
        state.readiness = "pending"

    state.mark_step(IntentStepId.S12_READY.value)
    return StepResult(step_id=IntentStepId.S12_READY, outcome=StepOutcome.COMPLETE)
