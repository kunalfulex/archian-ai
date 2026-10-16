"""S02 — turn type from classifier snapshot."""
from __future__ import annotations

from services.intent.state import IntentState
from services.intent.steps.goal import run_step_goal
from services.intent.types import IntentStepId, StepOutcome, StepResult, TurnType


def _normalize_turn_type(raw: str) -> str:
    intent = (raw or "").lower().strip()
    mapping = {
        "extract": TurnType.EXTRACT.value,
        "migrate": TurnType.MIGRATE.value,
        "migrate_data": TurnType.MIGRATE.value,
        "map_data": TurnType.MAP.value,
        "map": TurnType.MAP.value,
        "info": TurnType.INFO.value,
        "general": TurnType.GENERAL.value,
        "metadata_deploy": TurnType.METADATA_DEPLOY.value,
        "deploy": TurnType.DEPLOY.value,
        "deploy_schema": TurnType.DEPLOY.value,
        "join": TurnType.JOIN.value,
        "create": TurnType.CREATE.value,
        "upsert": TurnType.UPSERT.value,
        "update": TurnType.UPDATE.value,
        "search": TurnType.SEARCH.value,
        "transform": TurnType.DATA_OPS.value,
        "filter": TurnType.DATA_OPS.value,
        "dedupe": TurnType.DATA_OPS.value,
        "validate": TurnType.DATA_OPS.value,
        "cleanup": TurnType.DATA_OPS.value,
        "multi": TurnType.DATA_OPS.value,
    }
    return mapping.get(intent, intent or TurnType.UNKNOWN.value)


async def run_step_turn_type(state: IntentState) -> StepResult:
    snap = state.classifier_snapshot or {}
    raw_intent = snap.get("intent") or ""
    state.turn_type = _normalize_turn_type(str(raw_intent))
    state.conversational = bool(snap.get("conversational_turn"))
    state.off_topic = bool(snap.get("off_topic"))

    if bool(snap.get("multi_source_extract")):
        state.turn_type = TurnType.DATA_OPS.value
    elif str(raw_intent).lower() == "join":
        state.turn_type = TurnType.JOIN.value
    elif state.turn_type == TurnType.UNKNOWN.value and str(raw_intent).lower() == "metadata_deploy":
        state.turn_type = TurnType.METADATA_DEPLOY.value
    elif state.turn_type == TurnType.UNKNOWN.value:
        for op, turn in (
            ("create", TurnType.CREATE.value),
            ("upsert", TurnType.UPSERT.value),
            ("update", TurnType.UPDATE.value),
            ("search", TurnType.SEARCH.value),
            ("deploy_schema", TurnType.DEPLOY.value),
            ("deploy", TurnType.DEPLOY.value),
        ):
            if op in state.operations:
                state.turn_type = turn
                break

    if state.turn_type == TurnType.JOIN.value and "join" not in state.operations:
        state.operations = ["join"] + list(state.operations)

    state.mark_step(IntentStepId.S02_TURN_TYPE.value)
    return StepResult(step_id=IntentStepId.S02_TURN_TYPE, outcome=StepOutcome.COMPLETE)


async def run_step_turn_and_goal(state: IntentState) -> StepResult:
    """S02+S03 combined — one pipeline tick for turn type and goal summary."""
    await run_step_turn_type(state)
    result = await run_step_goal(state)
    state.mark_step(IntentStepId.S03_GOAL.value)
    return result
