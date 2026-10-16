"""S11 — sync plan sketch metadata from execution plan."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.readiness import can_build_plan
from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult


async def run_step_plan_sketch(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    cls = classification or {}
    ep = cls.get("execution_plan") or {}
    if isinstance(ep, dict):
        steps = ep.get("steps") or []
        state.plan_id = state.plan_id or (ep.get("plan_id") or cls.get("plan_id"))
        state.plan_status = state.plan_status or ep.get("status") or cls.get("plan_status")
        state.plan_step_count = len(steps)
    summary = cls.get("plan_steps_summary") or []
    if summary and not state.plan_step_count:
        state.plan_step_count = len(summary)

    if state.plan_step_count > 0 and (state.plan_status or "") in (
        "awaiting_approval",
        "draft",
        "approved",
    ):
        if not state.gaps and not state.active_gap:
            state.readiness = "ready_to_plan"
    elif can_build_plan(state, cls, prompt=state.prompt) and not state.gaps:
        state.readiness = "ready_to_plan"

    state.mark_step(IntentStepId.S11_PLAN_SKETCH.value)
    return StepResult(step_id=IntentStepId.S11_PLAN_SKETCH, outcome=StepOutcome.COMPLETE)
