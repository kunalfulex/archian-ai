"""Tests for unified extract field/column clarification."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.extract_field_clarification import (
    apply_field_clarification_selection,
    apply_field_clarification_selections,
    build_field_clarification_options,
    fields_explicit_in_prompt,
    finalize_extract_field_clarification,
    hints_look_destination_invented,
    is_vague_extract_prompt,
    needs_extract_field_clarification,
    parse_field_clarification_reply,
)
from services.planning.clarification_queue import clarification_from_extract_fields_intent
from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.plan_builder import _extract_step_label
from services.planning.schemas import ExecutionPlan


def test_vague_prompt_triggers_field_clarification():
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        field_hints=["first name", "last name", "phone"],
        field_hints_strict=True,
        cross_system=True,
    )
    prompt = "deploy general user data from user table of Test DB to contact object"
    assert is_vague_extract_prompt(prompt)
    assert hints_look_destination_invented(
        intent.field_hints, cross_system=True, prompt=prompt
    )
    assert needs_extract_field_clarification(intent, prompt)


def test_deploy_user_data_prompt_is_vague_and_needs_clarification():
    """Regression: 'application, to' must not block vague detection."""
    prompt = (
        "deploy user data from user table of Test DB application, "
        "to salesforce org contact object"
    )
    assert is_vague_extract_prompt(prompt)
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        field_hints=[],
        field_hints_strict=False,
        cross_system=True,
    )
    assert needs_extract_field_clarification(intent, prompt)


def test_deploy_user_data_with_vague_scope_needs_clarification():
    """Decomposer scope=vague should trigger picker without regex fallback."""
    prompt = (
        "deploy user data from user table of Test DB application, "
        "to salesforce org contact object"
    )
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="vague",
        field_scope_confidence=0.95,
        field_hints=[],
        field_hints_strict=False,
        cross_system=True,
    )
    assert needs_extract_field_clarification(intent, prompt)


def test_explicit_fields_skip_clarification():
    """Decomposer explicit scope skips picker — prompt regex is not a fallback."""
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        extract_field_scope="explicit",
        field_scope_confidence=0.95,
        field_hints=["first name", "last name", "phone"],
        field_hints_strict=True,
        cross_system=True,
    )
    prompt = "get contact details like first name, last name, phone from SF 1"
    assert fields_explicit_in_prompt(prompt, intent.field_hints)
    assert not needs_extract_field_clarification(intent, prompt)


def test_full_detail_sql_extract_prompt_skips_field_clarification():
    """Decomposer explicit scope must skip pick_extract_fields (no prompt regex fallback)."""
    prompt = (
        "extract first name, last name and email from user table from Test DB database"
    )
    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="explicit",
        field_scope_confidence=0.95,
        field_hints=["first name", "last name", "email"],
        field_hints_strict=True,
        cross_system=False,
    )
    assert not needs_extract_field_clarification(intent, prompt)


def test_inferred_hints_without_strict_need_clarification():
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        field_hints=["email"],
        field_hints_strict=False,
        cross_system=True,
    )
    assert needs_extract_field_clarification(intent, "extract user table to salesforce")


def test_extract_step_label_hides_unconfirmed_hints():
    classification = {
        "source_name": "Test DB",
        "plan_contract": {
            "source": {"entity_key": "user", "catalog_label": "user"},
            "migration_intent": {
                "field_hints": ["first name", "last name", "phone"],
                "field_hints_strict": False,
            },
        },
    }
    label = _extract_step_label("Test DB", "sql", classification)
    assert "first name" not in label
    assert "user" in label


def test_extract_step_label_shows_strict_hints():
    classification = {
        "source_name": "Test DB",
        "plan_contract": {
            "source": {"entity_key": "user", "catalog_label": "user"},
            "migration_intent": {
                "field_hints": ["email", "phone"],
                "field_hints_strict": True,
            },
        },
    }
    label = _extract_step_label("Test DB", "sql", classification)
    assert "email" in label


def test_apply_field_clarification_all_descriptive():
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        field_hints=["first name"],
        field_hints_strict=True,
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    updated = apply_field_clarification_selection(
        intent, option_id="field_all_descriptive"
    )
    assert updated.extract_fields_confirmed
    assert updated.field_hints == []
    assert updated.field_hints_strict is False
    assert updated.extract_field_scope == "all_descriptive"
    assert not updated.needs_extract_clarification


def test_apply_field_clarification_single_column():
    options = build_field_clarification_options(["email", "phone"], entity_label="user")
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        suggested_field_hints=["email", "phone"],
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    email_opt = next(o for o in options if o["label"] == "email")
    updated = apply_field_clarification_selection(
        intent, option_id=email_opt["id"], options=options
    )
    assert updated.field_hints == ["email"]
    assert updated.field_hints_strict is True
    assert updated.extract_field_scope == "explicit"


def test_apply_field_clarification_multi_column():
    options = build_field_clarification_options(
        ["email", "first_name", "phone"], entity_label="user"
    )
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        suggested_field_hints=["email", "first_name", "phone"],
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    email_opt = next(o for o in options if o["label"] == "email")
    name_opt = next(o for o in options if o["label"] == "first_name")
    updated = apply_field_clarification_selections(
        intent,
        option_ids=[email_opt["id"], name_opt["id"]],
        options=options,
    )
    assert updated.field_hints == ["email", "first_name"]
    assert updated.extract_fields_confirmed is True
    assert not updated.needs_extract_clarification


def test_parse_field_clarification_reply_comma_list():
    options = build_field_clarification_options(["email"], entity_label="user")
    parsed = parse_field_clarification_reply("name, email, phone", options)
    assert parsed
    assert parsed["option_id"] == "fields_custom"
    assert "name" in parsed["text_value"]


def test_append_extract_field_clarification_options():
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        suggested_field_hints=["email", "first_name"],
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(
                ambiguity_type="pick_extract_fields",
                prompt="Which columns should we extract from user?",
            )
        ],
    )
    clar = clarification_from_extract_fields_intent(intent)
    assert clar is not None
    plan = ExecutionPlan(clarifications=[clar])
    field_clar = next(
        c for c in plan.clarifications if c.clarification_type == "pick_extract_fields"
    )
    labels = {o.label for o in field_clar.options}
    assert "email" in labels
    assert "All descriptive columns (recommended)" in labels


def test_finalize_extract_field_clarification_clears_invented_hints():
    intent = PromptMigrationIntent(
        driving_entity_key="user",
        driving_entity_label="user",
        field_hints=["first name", "last name", "phone"],
        field_hints_strict=True,
        cross_system=True,
    )
    prompt = "deploy general user data from user table of Test DB to contact object"

    async def _run():
        with patch(
            "services.planning.extract_field_clarification.load_source_field_labels",
            new=AsyncMock(return_value=["email", "first_name", "last_name"]),
        ):
            return await finalize_extract_field_clarification(
                intent,
                user_id="user-1",
                classification={"source": "sql", "source_name": "Test DB"},
                prompt=prompt,
            )

    result = asyncio.run(_run())
    assert result.needs_extract_clarification
    assert result.field_hints == []
    assert result.field_hints_strict is False
    assert "email" in result.suggested_field_hints
    assert any(a.ambiguity_type == "pick_extract_fields" for a in result.extract_ambiguities)


def test_validate_field_hints_against_catalog_mismatch():
    import asyncio
    from unittest.mock import AsyncMock, patch

    from services.planning.extract_field_clarification import (
        apply_catalog_field_validation,
    )

    intent = PromptMigrationIntent(
        driving_entity_key="users",
        driving_entity_label="users",
        extract_field_scope="explicit",
        field_scope_confidence=0.9,
        field_hints=["FirstName", "Phone"],
        field_hints_strict=True,
        cross_system=True,
    )

    async def _run():
        with patch(
            "services.planning.extract_field_clarification.validate_field_hints_against_catalog",
            new=AsyncMock(
                return_value={
                    "valid": False,
                    "matched": [],
                    "unmatched": ["FirstName", "Phone"],
                    "catalog_labels": ["email", "first_name", "last_name"],
                }
            ),
        ):
            return await apply_catalog_field_validation(
                intent,
                user_id="user-1",
                classification={"source": "sql"},
            )

    result = asyncio.run(_run())
    assert result.extract_field_scope == "vague"
    assert result.field_hints == []
    assert "email" in result.suggested_field_hints


def test_resolve_field_clarification_uses_plan_contract_entity():
    from services.planning.extract_field_clarification import resolve_field_clarification_for_plan
    from services.planning.schemas import PlanEntityRef, PlanContract

    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="users",
            catalog_label="users",
            entity_label="users",
            resolution_source="plan_pinned",
            confidence=0.95,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            catalog_label="Contact",
            entity_label="Contact",
            resolution_source="plan_pinned",
            confidence=0.95,
        ),
    )
    prompt = "deploy general user data from user table of Test DB to contact object"

    async def _run():
        with patch(
            "services.planning.extract_field_clarification.load_source_field_labels",
            new=AsyncMock(return_value=["email", "first_name", "last_name"]),
        ):
            return await resolve_field_clarification_for_plan(
                None,
                user_id="user-1",
                classification={
                    "source": "sql",
                    "source_name": "Test DB",
                    "destination": "salesforce",
                },
                prompt=prompt,
                plan_contract=contract,
            )

    result = asyncio.run(_run())
    assert result is not None
    assert result.needs_extract_clarification
    assert result.driving_entity_key == "users"
    assert any(a.ambiguity_type == "pick_extract_fields" for a in result.extract_ambiguities)


def test_resolve_field_clarification_overrides_stale_driving_key():
    from services.planning.extract_field_clarification import resolve_field_clarification_for_plan
    from services.planning.schemas import PlanContract, PlanEntityRef

    stale_intent = PromptMigrationIntent(
        driving_entity_key="RecordType",
        driving_entity_label="Record Types",
        extract_field_scope="vague",
        field_scope_confidence=0.9,
        cross_system=True,
    )
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="user",
            catalog_label="user",
            entity_label="user",
            resolution_source="catalog_label",
            confidence=0.9,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            entity_key="Contact",
            catalog_label="Contact",
            entity_label="Contact",
            resolution_source="catalog_label",
            confidence=0.9,
        ),
    )

    async def _run():
        with patch(
            "services.planning.extract_field_clarification.load_source_field_labels",
            new=AsyncMock(return_value=["email", "first_name"]),
        ):
            return await resolve_field_clarification_for_plan(
                stale_intent,
                user_id="user-1",
                classification={"source": "sql", "source_name": "Test DB"},
                prompt="deploy user data to salesforce",
                plan_contract=contract,
            )

    result = asyncio.run(_run())
    assert result.driving_entity_key == "user"
    assert "user" in (result.extract_ambiguities[0].prompt or "").lower()
