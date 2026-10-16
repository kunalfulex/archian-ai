"""S07 — source table/object/tab from plan_contract (binding) or catalog fallback."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import EntityRef, IntentState
from services.intent.types import IntentStepId, StepOutcome, StepResult
from services.planning.binding.guards import (
    BINDING_RESOLUTION_SOURCES,
    binding_side_ref,
    entity_side_resolved_by_binding,
    plan_contract_from_classification,
)
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import PlanEntityRef, normalize_connector_type


def _classification_working_copy(
    state: IntentState,
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge IntentState connector + entity fields into a resolver-friendly dict."""
    out = dict(classification or {})
    if state.source.connector_type and state.source.connector_type != "unknown":
        out["source"] = state.source.connector_type
    if state.source.display_name:
        out["source_name"] = state.source.display_name
    if state.source.connector_id:
        out["source_connector_id"] = state.source.connector_id
    if state.source.sheet_name:
        out["source_sheet_name"] = state.source.sheet_name
    if state.source.spreadsheet_id:
        out["source_spreadsheet_id"] = state.source.spreadsheet_id
    if state.destination.connector_type and state.destination.connector_type != "unknown":
        out["destination"] = state.destination.connector_type
    if state.destination.display_name:
        out["destination_name"] = state.destination.display_name
    if state.destination.connector_id:
        out["destination_connector_id"] = state.destination.connector_id
    if state.destination.sheet_name:
        out["destination_sheet_name"] = state.destination.sheet_name
    if state.destination.spreadsheet_id:
        out["destination_spreadsheet_id"] = state.destination.spreadsheet_id
    if state.destination_same_as_source:
        out["destination_same_as_source"] = True
    return out


def _apply_entity_ref_to_state(state: IntentState, ref: PlanEntityRef) -> None:
    if not ref.is_resolved():
        return
    state.source_entity = EntityRef(
        entity_key=ref.entity_key,
        entity_label=ref.entity_label or ref.catalog_label or ref.entity_key,
        confidence=float(ref.confidence or 0.0),
        resolution_source=ref.resolution_source or "catalog_label",
    )


def _apply_entity_ref_to_classification(cls: Dict[str, Any], ref: PlanEntityRef) -> None:
    if not ref.is_resolved():
        return
    ct = normalize_connector_type(ref.connector_type or cls.get("source") or "")
    key = (ref.entity_key or "").strip()
    label = (ref.entity_label or ref.catalog_label or key).strip()
    if ct == "sql":
        cls["source_table_name"] = key
    elif ct == "salesforce":
        cls["source_object_api_name"] = key
    elif ct == "google_sheets":
        cls["source_sheet_name"] = key
    if key:
        cls["planner_source_entity_confirmed"] = True


def _state_has_binding_source(state: IntentState) -> bool:
    src = (state.source_entity.resolution_source or "").strip()
    return src in BINDING_RESOLUTION_SOURCES and bool(state.source_entity.entity_key)


async def _resolve_source_via_catalog(
    state: IntentState,
    classification: Dict[str, Any],
    *,
    user_id: Optional[str],
) -> Optional[PlanEntityRef]:
    if not user_id or not state.source.resolved:
        return None
    connector_id = (state.source.connector_id or classification.get("source_connector_id") or "").strip()
    if not connector_id:
        return None
    ct = normalize_connector_type(state.source.connector_type or classification.get("source") or "")
    if ct not in ("salesforce", "sql", "google_sheets"):
        return None

    from services.intent.migration_intent_view import read_migration_intent_dict
    from services.planning.entity_resolver import _resolve_side_entity
    from services.planning.plan_scope import read_plan_scope

    working = _classification_working_copy(state, classification)
    parsed_goal = read_plan_scope(working) or None
    migration_intent = read_migration_intent_dict(working, state=state)

    return await _resolve_side_entity(
        user_id=str(user_id),
        classification=working,
        role="source",
        prompt=state.prompt or "",
        parsed_goal=parsed_goal,
        migration_intent=migration_intent or None,
    )


def _apply_heuristic_entity(
    state: IntentState,
    classification: Dict[str, Any],
) -> Optional[str]:
    """Fast path when plan_contract binding is absent (legacy / partial classification)."""
    cls = classification or {}
    key: Optional[str] = None
    label: Optional[str] = None
    confidence = 0.0
    source_tag = "prompt"

    if cls.get("planner_source_entity_confirmed"):
        table = (cls.get("source_table_name") or "").strip()
        obj = (cls.get("source_object_api_name") or "").strip()
        sheet = (cls.get("source_sheet_name") or "").strip()
        if table:
            key, label, confidence, source_tag = table, table, 0.95, "classification"
        elif obj:
            key, label, confidence, source_tag = obj, obj, 0.95, "classification"
        elif sheet:
            key, label, confidence, source_tag = sheet, sheet, 0.9, "classification"

    if not key and state.source_entity.entity_key:
        key = state.source_entity.entity_key
        label = state.source_entity.entity_label or key
        confidence = state.source_entity.confidence or 0.85
        source_tag = state.source_entity.resolution_source or "intent_state"

    if not key:
        from services.intent.migration_intent_view import read_migration_intent_dict

        mi = read_migration_intent_dict(cls, state=state)
        dk = (mi.get("driving_entity_key") or "").strip()
        if dk:
            key = dk
            label = (mi.get("driving_entity_label") or dk).strip()
            confidence = 0.85
            source_tag = "migration_intent"

    if not key:
        return None

    state.source_entity.entity_key = key
    state.source_entity.entity_label = label or key
    state.source_entity.confidence = confidence
    state.source_entity.resolution_source = source_tag
    return key


async def run_step_entity(
    state: IntentState,
    *,
    classification: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
    catalog: Optional[ResourceCatalog] = None,
) -> StepResult:
    del catalog
    cls = dict(classification or {})

    bound_ref = binding_side_ref(cls, "source")
    if entity_side_resolved_by_binding(bound_ref):
        _apply_entity_ref_to_state(state, bound_ref)
        _apply_entity_ref_to_classification(cls, bound_ref)
        state.mark_step(IntentStepId.S07_ENTITY.value)
        return StepResult(step_id=IntentStepId.S07_ENTITY, outcome=StepOutcome.COMPLETE)

    contract = plan_contract_from_classification(cls)
    if contract and entity_side_resolved_by_binding(contract.source):
        _apply_entity_ref_to_state(state, contract.source)
        _apply_entity_ref_to_classification(cls, contract.source)
        state.mark_step(IntentStepId.S07_ENTITY.value)
        return StepResult(step_id=IntentStepId.S07_ENTITY, outcome=StepOutcome.COMPLETE)

    from services.planning.planner_path import classification_has_plan_scope

    if classification_has_plan_scope(cls):
        if cls.get("planner_source_entity_confirmed"):
            table = (cls.get("source_table_name") or "").strip()
            obj = (cls.get("source_object_api_name") or "").strip()
            sheet = (cls.get("source_sheet_name") or "").strip()
            key = table or obj or sheet
            if key:
                state.source_entity.entity_key = key
                state.source_entity.entity_label = key
                state.source_entity.confidence = 0.95
                state.source_entity.resolution_source = "clarification"
    else:
        _apply_heuristic_entity(state, cls)

    if not _state_has_binding_source(state):
        catalog_ref = await _resolve_source_via_catalog(
            state,
            cls,
            user_id=user_id,
        )
        if catalog_ref and catalog_ref.is_resolved():
            if not entity_side_resolved_by_binding(catalog_ref):
                prev_conf = float(state.source_entity.confidence or 0.0)
                if float(catalog_ref.confidence or 0.0) >= prev_conf:
                    _apply_entity_ref_to_state(state, catalog_ref)
                    _apply_entity_ref_to_classification(cls, catalog_ref)

    state.mark_step(IntentStepId.S07_ENTITY.value)
    return StepResult(step_id=IntentStepId.S07_ENTITY, outcome=StepOutcome.COMPLETE)
