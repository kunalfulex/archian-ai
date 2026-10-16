"""Shared helpers — when S12 must stay on planner path despite conversational labels."""
from __future__ import annotations

from typing import Any, Dict

from services.intent.readiness import can_build_plan, destination_blocks_plan
from services.intent.state import IntentState
from services.intent.types import DestinationNeed


def pipeline_routing_active(
    state: IntentState,
    classification: Dict[str, Any],
    *,
    prompt: str | None = None,
) -> bool:
    """True when planner path must not fall through to general/info chat."""
    cls = dict(classification or {})
    if state.gaps or state.active_gap:
        return True
    if cls.get("needs_clarification"):
        return True
    if cls.get("clarifications"):
        return True
    from services.planning.planner_path import classification_has_plan_scope

    if classification_has_plan_scope(cls):
        return True
    if can_build_plan(state, cls, prompt=prompt or state.prompt):
        return True
    if destination_blocks_plan(state):
        return True
    if state.needs_destination == DestinationNeed.REQUIRED.value and not state.destination.resolved:
        return True
    return False
