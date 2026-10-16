"""Structured logging for intent pipeline steps."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome

logger = logging.getLogger("edison.intent_pipeline")


def intent_state_snapshot(state: Optional[IntentState]) -> Dict[str, Any]:
    if state is None:
        return {}
    return state.summary_snapshot()


def log_intent_step(
    step_id: IntentStepId | str,
    state: IntentState,
    *,
    outcome: StepOutcome | str = StepOutcome.COMPLETE,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    message: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    duration_ms: Optional[float] = None,
) -> None:
    del user_id, conversation_id, extra
    sid = step_id.value if isinstance(step_id, IntentStepId) else str(step_id)
    if duration_ms is not None:
        state.step_timings_ms[sid] = round(duration_ms, 2)
    ms = f" ms={duration_ms:.0f}" if duration_ms is not None else ""
    oc = outcome.value if isinstance(outcome, StepOutcome) else outcome
    snap = intent_state_snapshot(state)
    entity = snap.get("source_entity") or "-"
    scope = snap.get("field_scope") or "-"
    route = snap.get("route") or "-"
    extra_s = f" {message}" if message else ""
    logger.info(
        "[intent_pipeline] step=%s outcome=%s entity=%s scope=%s route=%s%s%s",
        sid,
        oc,
        entity,
        scope,
        route,
        extra_s,
        ms,
    )


class StepTimer:
    """Context manager for per-step duration logging."""

    def __init__(
        self,
        step_id: IntentStepId,
        state: IntentState,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ):
        self.step_id = step_id
        self.state = state
        self.user_id = user_id
        self.conversation_id = conversation_id
        self._t0 = 0.0
        self.outcome = StepOutcome.COMPLETE
        self.message: Optional[str] = None

    def __enter__(self) -> "StepTimer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        duration_ms = (time.perf_counter() - self._t0) * 1000.0
        log_intent_step(
            self.step_id,
            self.state,
            outcome=self.outcome,
            user_id=self.user_id,
            conversation_id=self.conversation_id,
            message=self.message,
            duration_ms=duration_ms,
        )
