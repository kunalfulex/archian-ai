"""S04 — whether destination connector/entity is required for this turn."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.intent.state import IntentState
from services.intent.types import DestinationNeed, IntentStepId, StepOutcome, StepResult, TurnType


def _pipeline_tokens_from_state(state: IntentState, snap: Dict[str, Any]) -> Optional[List[str]]:
    tokens = snap.get("_pipeline_tokens")
    if isinstance(tokens, list) and tokens:
        return [str(t).lower() for t in tokens]
    return None


def derive_destination_need(state: IntentState) -> str:
    """Rules-only: scope tokens and continuation phase drive destination need."""
    snap = state.classifier_snapshot or {}
    tokens = _pipeline_tokens_from_state(state, snap)
    if tokens is not None:
        if not any(t in tokens for t in ("map", "migrate")):
            return DestinationNeed.NONE.value
        if state.continuation_turn or snap.get("active_job_id"):
            return DestinationNeed.INHERIT_SESSION.value
        return DestinationNeed.REQUIRED.value

    turn = (state.turn_type or "").lower()
    ops = [str(o).lower() for o in (state.operations or [])]
    snap = state.classifier_snapshot or {}
    src = (state.source_type or snap.get("source") or "").lower()
    dest = (state.destination_type or snap.get("destination") or "").lower()
    dest_name = (snap.get("destination_name") or state.destination.display_name or "").strip()
    dest_phrase = (snap.get("destination_phrase") or "").strip()

    if turn in (TurnType.GENERAL.value, TurnType.INFO.value):
        return DestinationNeed.NONE.value

    if turn in (
        TurnType.CREATE.value,
        TurnType.UPSERT.value,
        TurnType.DEPLOY.value,
        TurnType.METADATA_DEPLOY.value,
    ):
        return DestinationNeed.REQUIRED.value

    if turn in (TurnType.JOIN.value, TurnType.SEARCH.value, TurnType.UPDATE.value):
        return DestinationNeed.NONE.value

    if state.continuation_turn and turn in (
        TurnType.MAP.value,
        TurnType.MIGRATE.value,
        TurnType.DATA_OPS.value,
    ):
        return DestinationNeed.INHERIT_SESSION.value

    # Cross-system extract/export — before extract-only shortcut
    if src and dest and src != dest and dest not in ("unknown", ""):
        from services.planning.connector_guards import is_known_connector

        if is_known_connector(dest):
            return DestinationNeed.REQUIRED.value
        if (
            (dest_name and dest_name.lower() != "unknown")
            or dest_phrase
            or snap.get("planner_destination_confirmed")
            or snap.get("planner_create_spreadsheet")
        ):
            return DestinationNeed.REQUIRED.value

    if turn == TurnType.EXTRACT.value and not any(o in ops for o in ("migrate", "map")):
        if not dest_name and not dest_phrase and not state.destination.display_name:
            if dest in ("unknown", "", "none") or not dest:
                return DestinationNeed.NONE.value

    if turn in (TurnType.MAP.value, TurnType.MIGRATE.value, TurnType.METADATA_DEPLOY.value):
        return DestinationNeed.REQUIRED.value

    if any(o in ops for o in ("migrate", "map")):
        return DestinationNeed.REQUIRED.value

    if snap.get("destination_same_as_source"):
        return DestinationNeed.INHERIT_SESSION.value

    if turn == TurnType.EXTRACT.value:
        return DestinationNeed.NONE.value

    return DestinationNeed.NONE.value


async def run_step_destination_need(state: IntentState) -> StepResult:
    state.needs_destination = derive_destination_need(state)

    if state.needs_destination == DestinationNeed.NONE.value:
        state.destination_type = "unknown"
        state.destination = state.destination.model_copy(
            update={"connector_type": "unknown", "display_name": None, "resolved": False}
        )

    state.mark_step(IntentStepId.S04_DESTINATION_NEED.value)
    return StepResult(step_id=IntentStepId.S04_DESTINATION_NEED, outcome=StepOutcome.COMPLETE)
