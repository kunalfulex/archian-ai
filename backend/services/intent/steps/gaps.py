"""S10 — surface blocking gaps from classification / plan state."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.gaps import detect_planner_gaps
from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult
from services.planning.resource_catalog import ResourceCatalog


async def run_step_gaps(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
    catalog: Optional[ResourceCatalog] = None,
    user_id: Optional[str] = None,
    prompt: Optional[str] = None,
) -> StepResult:
    cls = classification or {}
    gaps, clarifications = await detect_planner_gaps(
        state,
        cls,
        prompt=prompt or state.prompt,
        catalog=catalog,
        user_id=user_id,
    )

    state.gaps = gaps
    state.active_gap = gaps[0]["clarification_type"] if gaps else None
    state.gap_clarifications = [
        c.model_dump(mode="json") for c in clarifications
    ] if clarifications else []
    if gaps:
        state.readiness = "need_clarification"

    state.mark_step(IntentStepId.S10_GAPS.value)
    outcome = StepOutcome.BLOCKED if gaps else StepOutcome.COMPLETE
    return StepResult(step_id=IntentStepId.S10_GAPS, outcome=outcome)
