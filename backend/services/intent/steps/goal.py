"""S03 — goal summary from classifier reasoning or prompt excerpt."""
from __future__ import annotations

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult


async def run_step_goal(state: IntentState) -> StepResult:
    snap = state.classifier_snapshot or {}
    reasoning = (snap.get("reasoning") or "").strip()
    if reasoning:
        state.goal_summary = reasoning[:200]
    elif state.prompt:
        state.goal_summary = state.prompt.strip()[:200]

    state.mark_step(IntentStepId.S03_GOAL.value)
    return StepResult(step_id=IntentStepId.S03_GOAL, outcome=StepOutcome.COMPLETE)
