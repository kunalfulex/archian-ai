"""S08 — extract field scope from binding migration_intent (authoritative when bound)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult
from services.planning.binding.guards import (
    plan_contract_from_classification,
    source_resolved_by_binding,
)


def _apply_migration_intent_fields(state: IntentState, mi: Any) -> None:
    state.fields.scope = mi.extract_field_scope or "unspecified"
    state.fields.scope_confidence = float(mi.field_scope_confidence or 0.0)
    state.fields.hints = list(mi.field_hints or [])
    state.fields.hints_strict = bool(mi.field_hints_strict)
    state.fields.confirmed = bool(mi.extract_fields_confirmed)


async def run_step_field_scope(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    cls = dict(classification or {})
    from services.intent.migration_intent_view import read_migration_intent_model

    contract = plan_contract_from_classification(cls)
    binding_locked = source_resolved_by_binding(contract)

    mi = read_migration_intent_model(cls, state=state)

    if mi:
        _apply_migration_intent_fields(state, mi)
        if binding_locked and mi.extract_fields_confirmed:
            state.fields.confirmed = True
        elif not state.fields.confirmed and state.source_entity.entity_key:
            if binding_locked and mi.field_hints:
                state.fields.confirmed = True
            else:
                from services.planning.extract_field_clarification import (
                    needs_extract_field_clarification,
                )

                if not needs_extract_field_clarification(mi, state.prompt or ""):
                    state.fields.confirmed = True
    elif cls.get("extract_fields_confirmed"):
        state.fields.confirmed = True

    if binding_locked and contract and contract.migration_intent:
        raw = contract.migration_intent if isinstance(contract.migration_intent, dict) else {}
        hints = raw.get("field_hints") or []
        if hints and not state.fields.hints:
            state.fields.hints = list(hints)
        scope = (raw.get("extract_field_scope") or "").strip()
        if scope and state.fields.scope in ("unspecified", "", None):
            state.fields.scope = scope
        if raw.get("extract_fields_confirmed"):
            state.fields.confirmed = True

    state.mark_step(IntentStepId.S08_FIELD_SCOPE.value)
    return StepResult(step_id=IntentStepId.S08_FIELD_SCOPE, outcome=StepOutcome.COMPLETE)
