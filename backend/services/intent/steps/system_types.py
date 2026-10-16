"""S05 — connector system types from bound resources or classifier."""
from __future__ import annotations

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult
from services.planning.schemas import normalize_connector_type


async def run_step_system_types(state: IntentState) -> StepResult:
    snap = state.classifier_snapshot or {}

    src = normalize_connector_type(
        state.source.connector_type or state.source_type or snap.get("source") or ""
    )
    if src and src != "unknown":
        state.source_type = src

    dest = normalize_connector_type(
        state.destination.connector_type or state.destination_type or snap.get("destination") or ""
    )
    if dest and dest != "unknown":
        state.destination_type = dest

    state.mark_step(IntentStepId.S05_SYSTEM_TYPES.value)
    return StepResult(step_id=IntentStepId.S05_SYSTEM_TYPES, outcome=StepOutcome.COMPLETE)
