"""Tests for canonical migration_intent access (P5.4)."""
from __future__ import annotations

from services.intent.migration_intent_view import (
    migration_intent_dict_from_state,
    read_migration_intent_dict,
    read_migration_intent_model,
)
from services.intent.state import EntityRef, FieldSpec, IntentState


def test_read_prefers_intent_state_over_stale_legacy():
    state = IntentState(
        prompt="extract email from users",
        source_entity=EntityRef(entity_key="users", entity_label="users"),
        fields=FieldSpec(
            scope="explicit",
            scope_confidence=0.95,
            hints=["email"],
            hints_strict=True,
            confirmed=True,
        ),
    )
    cls = {
        "migration_intent": {
            "driving_entity_key": "accounts",
            "extract_field_scope": "vague",
        },
    }
    mi = read_migration_intent_dict(cls, state=state)
    assert mi["driving_entity_key"] == "users"
    assert mi["extract_field_scope"] == "explicit"
    assert mi["extract_fields_confirmed"] is True


def test_read_from_intent_state_snapshot_on_classification():
    state = IntentState(
        prompt="extract",
        fields=FieldSpec(scope="vague", scope_confidence=0.7),
        source_entity=EntityRef(entity_key="users"),
    )
    cls = {"intent_state": state.model_dump(mode="json")}
    mi = read_migration_intent_dict(cls)
    assert mi["driving_entity_key"] == "users"
    assert mi["extract_field_scope"] == "vague"


def test_migration_intent_model_round_trip():
    state = IntentState(
        prompt="x",
        fields=FieldSpec(scope="explicit", hints=["a"], hints_strict=True),
    )
    d = migration_intent_dict_from_state(state)
    model = read_migration_intent_model({"migration_intent": d}, state=state)
    assert model is not None
    assert model.extract_field_scope == "explicit"
    assert model.field_hints == ["a"]


def test_overlay_intent_state_on_classification():
    from services.intent.migration_intent_view import overlay_intent_state_on_classification
    from services.intent.state import EntityRef, FieldSpec, IntentState

    state = IntentState(
        prompt="extract email from users",
        turn_type="extract",
        operations=["extract", "transform"],
        source_type="sql",
        source_entity=EntityRef(entity_key="users", entity_label="users"),
        fields=FieldSpec(scope="explicit", hints=["email"], hints_strict=True, confirmed=True),
    )
    cls = {
        "source": "sql",
        "intent": "general",
        "migration_intent": {"driving_entity_key": "accounts", "extract_field_scope": "vague"},
        "intent_state": state.model_dump(mode="json"),
    }
    out = overlay_intent_state_on_classification(cls)
    assert out["source_table_name"] == "users"
    assert out["intent"] == "extract"
    assert out["operations"] == ["extract", "transform"]
    assert out["migration_intent"]["driving_entity_key"] == "users"
    assert out["migration_intent"]["extract_field_scope"] == "explicit"
