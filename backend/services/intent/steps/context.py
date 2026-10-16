"""S01 — conversation context (LLM session continuation vs fresh pipeline)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult


async def run_step_context(
    state: IntentState,
    *,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    intent_state_for_classifier: Optional[Dict[str, Any]] = None,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    from services.intent.session_continuation import (
        apply_session_decision_to_state,
        decision_from_classification,
        resolve_session_continuation,
    )

    cls = dict(classification or state.classifier_snapshot or {})
    decision = decision_from_classification(cls)
    if decision is None:
        decision = await resolve_session_continuation(
            state.prompt or "",
            user_id=str(state.user_id or ""),
            conversation_history=conversation_history,
            conversation_id=state.conversation_id,
            intent_state=intent_state_for_classifier,
            classification=cls,
        )

    apply_session_decision_to_state(state, decision)
    state.mark_step(IntentStepId.S01_CONTEXT.value)
    return StepResult(step_id=IntentStepId.S01_CONTEXT, outcome=StepOutcome.COMPLETE)
