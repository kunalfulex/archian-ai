"""Plan readiness from IntentState + classification snapshot."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.intent.types import DestinationNeed, TurnType
from services.planning.schemas import PlanContract


def can_build_plan(
    state: IntentState,
    classification: Optional[Dict[str, Any]] = None,
    *,
    prompt: Optional[str] = None,
) -> bool:
    """True when execution plan steps may be materialized or are already present."""
    cls = dict(classification or {})
    if state.gaps or state.active_gap:
        return False
    if cls.get("needs_clarification"):
        return False

    turn = (state.turn_type or "").lower()
    if turn in (TurnType.GENERAL.value, TurnType.INFO.value) or state.conversational:
        return False

    if state.plan_step_count > 0:
        return True

    from services.planning.plan_extract_gates import ready_to_build_migration_plan

    raw_contract = cls.get("plan_contract")
    plan_contract: Optional[PlanContract] = None
    if raw_contract:
        try:
            plan_contract = PlanContract.model_validate(raw_contract)
        except Exception:
            plan_contract = None

    from services.intent.migration_intent_view import read_migration_intent_dict

    migration_intent = read_migration_intent_dict(cls, state=state) or None
    parsed_goal = cls.get("_plan_scope") or {
        "operations": state.operations or cls.get("operations") or ["extract"],
    }
    pg = prompt or state.prompt or ""

    return ready_to_build_migration_plan(
        cls,
        plan_contract,
        migration_intent,
        pg,
        parsed_goal,
    )


def destination_blocks_plan(state: IntentState) -> bool:
    need = state.needs_destination or DestinationNeed.NONE.value
    return need == DestinationNeed.REQUIRED.value and not state.destination.resolved
