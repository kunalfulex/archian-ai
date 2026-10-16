"""Canonical migration-intent access (P5.4).

``IntentState`` (fields + source_entity) is the source of truth during the intent
pipeline. The legacy ``classification["migration_intent"]`` dict is a projection
for API/plan-builder consumers — read via :func:`read_migration_intent_dict` and
write only from :func:`project_to_classification` / plan metadata sync.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import IntentState
from services.planning.migration_intent_schemas import PromptMigrationIntent

# Deprecated for new code — use read_migration_intent_dict / IntentState instead.
LEGACY_MIGRATION_INTENT_KEY = "migration_intent"


def migration_intent_dict_from_state(state: IntentState) -> Dict[str, Any]:
    """Build a migration_intent-shaped dict from IntentState fields."""
    out: Dict[str, Any] = {}
    if state.source_entity.entity_key:
        out["driving_entity_key"] = state.source_entity.entity_key
        out["driving_entity_label"] = (
            state.source_entity.entity_label or state.source_entity.entity_key
        )
    scope = state.fields.scope
    if scope and scope != "unspecified":
        out["extract_field_scope"] = scope
        out["field_scope_confidence"] = state.fields.scope_confidence
    if state.fields.hints:
        out["field_hints"] = list(state.fields.hints)
    if state.fields.hints_strict:
        out["field_hints_strict"] = True
    if state.fields.confirmed:
        out["extract_fields_confirmed"] = True
    return out


def read_migration_intent_dict(
    classification: Dict[str, Any],
    *,
    state: Optional[IntentState] = None,
) -> Dict[str, Any]:
    """
    Read migration intent for planner steps.

    Prefers ``state`` when provided, then ``classification["intent_state"]``,
    then legacy ``classification["migration_intent"]``.
    """
    legacy = classification.get(LEGACY_MIGRATION_INTENT_KEY)
    base: Dict[str, Any] = dict(legacy) if isinstance(legacy, dict) else {}

    if state is not None:
        canonical = migration_intent_dict_from_state(state)
        if canonical:
            return {**base, **canonical}
        return base

    snap = classification.get("intent_state")
    if isinstance(snap, dict):
        try:
            hydrated = IntentState.model_validate(snap)
            canonical = migration_intent_dict_from_state(hydrated)
            if canonical:
                return {**base, **canonical}
        except Exception:
            pass

    return base


def read_migration_intent_model(
    classification: Dict[str, Any],
    *,
    state: Optional[IntentState] = None,
) -> Optional[PromptMigrationIntent]:
    data = read_migration_intent_dict(classification, state=state)
    if not data:
        return None
    try:
        return PromptMigrationIntent.model_validate(data)
    except Exception:
        return None


def overlay_intent_state_on_classification(classification: Dict[str, Any]) -> Dict[str, Any]:
    """
    Overlay canonical IntentState onto classification for plan build (P3.4).

    Prefer ``intent_state`` fields over stale classification keys for entity,
    field scope, and operations.
    """
    out = dict(classification or {})
    snap = out.get("intent_state")
    if not isinstance(snap, dict):
        return out

    try:
        state = IntentState.model_validate(snap)
    except Exception:
        return out

    if state.source_entity.entity_key:
        ct = (state.source_type or out.get("source") or "").lower()
        if ct == "sql":
            out["source_table_name"] = state.source_entity.entity_key
        elif ct == "salesforce":
            out["source_object_api_name"] = state.source_entity.entity_key
        elif ct in ("google_sheets", "gsheets"):
            out["source_sheet_name"] = state.source_entity.entity_key

    skip_ops_overlay = bool(
        out.get("_plan_scope")
        or out.get("_continuation_steps")
        or (
            (out.get("plan_route") or "") == "fresh_path"
            and not out.get("active_job_id")
        )
    )
    if state.operations and not skip_ops_overlay:
        out["operations"] = list(state.operations)
    if state.mode and not skip_ops_overlay:
        out["mode"] = state.mode
    if state.turn_type and not skip_ops_overlay:
        from services.intent.project import _intent_from_turn_type

        out["intent"] = _intent_from_turn_type(state.turn_type)

    mi = migration_intent_dict_from_state(state)
    if mi:
        existing = out.get("migration_intent")
        base = dict(existing) if isinstance(existing, dict) else {}
        out["migration_intent"] = {**base, **mi}

    if state.fields.confirmed:
        out["extract_fields_confirmed"] = True

    return out
