"""Tests for decomposer extract_field_scope normalization."""
from __future__ import annotations

from services.planning.extract_field_clarification import needs_extract_field_clarification
from services.planning.migration_intent_schemas import normalize_decomposed_field_scope
from services.planning.migration_intent_schemas import PromptMigrationIntent


def test_normalize_vague_scope_clears_hints():
    scope, conf, hints, strict = normalize_decomposed_field_scope(
        {"extract_field_scope": "vague", "field_scope_confidence": 0.95},
        prompt="deploy user data from user table to contact",
        field_hints=["first name", "last name"],
        field_hints_strict=True,
    )
    assert scope == "vague"
    assert conf == 0.95
    assert hints == []
    assert strict is False


def test_normalize_explicit_scope_keeps_concrete_hints():
    prompt = "extract first name, email from user table"
    scope, conf, hints, strict = normalize_decomposed_field_scope(
        {
            "extract_field_scope": "explicit",
            "field_scope_confidence": 0.9,
            "field_hints_strict": True,
        },
        prompt=prompt,
        field_hints=["first name", "email"],
        field_hints_strict=True,
    )
    assert scope == "explicit"
    assert hints == ["first name", "email"]
    assert strict is True


def test_normalize_unspecified_falls_back_to_vague_for_deploy_user_data():
    prompt = (
        "deploy user data from user table of Test DB application, "
        "to salesforce org contact object"
    )
    scope, conf, hints, strict = normalize_decomposed_field_scope(
        {"extract_field_scope": "unspecified", "field_scope_confidence": 0.5},
        prompt=prompt,
        field_hints=[],
        field_hints_strict=False,
    )
    assert scope == "vague"
    assert hints == []
    assert strict is False


def test_scope_vague_triggers_clarification_without_regex():
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="vague",
        field_scope_confidence=0.95,
        field_hints=[],
        field_hints_strict=False,
        cross_system=True,
    )
    prompt = "deploy user data from user table to contact"
    assert needs_extract_field_clarification(intent, prompt)


def test_scope_all_descriptive_skips_clarification():
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="all_descriptive",
        field_scope_confidence=0.9,
        cross_system=True,
    )
    assert not needs_extract_field_clarification(intent, "deploy user data")


def test_scope_explicit_with_hints_skips_clarification():
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="explicit",
        field_scope_confidence=0.9,
        field_hints=["email", "phone"],
        field_hints_strict=True,
        cross_system=True,
    )
    assert not needs_extract_field_clarification(intent, "extract email and phone from users")
