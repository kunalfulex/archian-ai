"""Tests for migration intent decomposition and catalog grounding."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.migration_intent import (
    _build_extraction_summary,
    _collect_extract_ambiguities,
    _finalize_destination_intent,
    _infer_destination_from_extract_spec,
)
from services.planning.migration_intent_schemas import EntityMention, PromptMigrationIntent
from services.planning.plan_builder import _extract_step_label


def test_infer_destination_from_extract_spec_sf_to_sf():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        cross_system=True,
    )
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "destination_name": "SF 2",
    }
    dest_catalog = [
        {"api_name": "Contact", "label": "Contact"},
        {"api_name": "Account", "label": "Account"},
    ]

    async def _run():
        with patch(
            "services.planning.migration_intent._load_destination_catalog_summary",
            new=AsyncMock(return_value=dest_catalog),
        ):
            return await _infer_destination_from_extract_spec(
                intent,
                user_id="user-1",
                classification=classification,
                prompt=(
                    "extract contact data first name, last name, email from SF 1 "
                    "and migrate them to SF 2 org"
                ),
            )

    inferred = asyncio.run(_run())
    assert inferred.destination_entity_key == "Contact"
    assert inferred.destination_entity_label == "Contact"


def test_infer_destination_skips_when_explicit_account_dest():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        destination_entity_key="Account",
        destination_entity_label="Account",
        cross_system=True,
    )
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "destination_object_api_name": "Account",
    }

    async def _run():
        return await _infer_destination_from_extract_spec(
            intent,
            user_id="user-1",
            classification=classification,
            prompt="migrate contacts to account object",
        )

    result = asyncio.run(_run())
    assert result.destination_entity_key == "Account"


def test_infer_destination_skips_for_sf_to_sql_without_catalog_match():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        cross_system=True,
    )
    classification = {"source": "salesforce", "destination": "sql"}

    async def _run():
        with patch(
            "services.planning.migration_intent._load_destination_catalog_summary",
            new=AsyncMock(return_value=[]),
        ):
            return await _infer_destination_from_extract_spec(
                intent,
                user_id="user-1",
                classification=classification,
                prompt="migrate contacts to sql",
            )

    result = asyncio.run(_run())
    assert result.destination_entity_key is None


def test_extract_step_label_includes_related_parent():
    classification = {
        "source_name": "SF 1",
        "plan_contract": {
            "source": {
                "entity_key": "Contact",
                "catalog_label": "Contact",
            },
            "migration_intent": {
                "include_related_data": True,
                "parent_entity_label": "Account",
                "field_hints": ["name", "phone"],
                "field_hints_strict": True,
            },
        },
    }
    label = _extract_step_label("SF 1", "salesforce", classification)
    assert "Contact" in label
    assert "Account" in label
    assert "name" in label


def test_needs_extract_clarification_on_ambiguous_shape():
    intent = PromptMigrationIntent(
        extract_shape="ambiguous",
        entity_mentions=[
            EntityMention(phrase="contacts", role="extract_primary"),
            EntityMention(
                phrase="account",
                role="ambiguous",
                confidence=0.5,
                ambiguity_reason="with_account_details_attach",
            ),
        ],
    )
    amb = _collect_extract_ambiguities(intent)
    assert amb
    intent = intent.model_copy(
        update={"needs_extract_clarification": True, "extract_ambiguities": amb}
    )
    assert intent.needs_extract_clarification


def test_build_extraction_summary_contact_only():
    summary = _build_extraction_summary(
        driving_label="Contact",
        field_hints=["first name", "last name", "phone"],
        include_related=False,
        field_hints_strict=True,
    )
    assert summary == "Extract Contact fields (first name, last name, phone)"
    assert "Account" not in summary


def test_build_extraction_summary_hides_unconfirmed_hints():
    summary = _build_extraction_summary(
        driving_label="user",
        field_hints=["first name", "last name", "phone"],
        include_related=False,
        field_hints_strict=False,
    )
    assert summary == "Extract from user"
    assert "first name" not in summary


def test_finalize_destination_resolves_account_from_prompt():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        destination_entity_phrase="account object",
        cross_system=True,
    )
    dest_catalog = [
        {"api_name": "Contact", "label": "Contact"},
        {"api_name": "Account", "label": "Account"},
    ]

    async def _run():
        with patch(
            "services.planning.migration_intent._load_destination_catalog_summary",
            new=AsyncMock(return_value=dest_catalog),
        ):
            return await _finalize_destination_intent(
                intent,
                user_id="user-1",
                classification={
                    "source": "salesforce",
                    "destination": "salesforce",
                    "destination_name": "SF 2",
                    "destination_object_api_name": "Account",
                },
                prompt=(
                    "get contact details like first name, last name, phone from SF 1 "
                    "and migrate them to SF 2 account object"
                ),
                parsed_goal={"destination_phrase": "account object", "entities": ["Contact", "Account"]},
            )

    result = asyncio.run(_run())
    assert result.destination_entity_key == "Account"


def test_resolve_plan_entities_uses_explicit_destination_entity():
    from services.planning.entity_resolver import resolve_plan_entities
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.schemas import PlanEntityRef

    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        destination_entity_key="Account",
        destination_entity_label="Account",
        cross_system=True,
    )

    async def fake_side(*, user_id, classification, role, prompt, parsed_goal, migration_intent=None):
        del user_id, classification, prompt, parsed_goal
        mi = migration_intent or {}
        if role == "destination":
            key = mi.get("destination_entity_key") or "Wrong"
        else:
            key = mi.get("driving_entity_key") or "Wrong"
        return PlanEntityRef(
            connector_type="salesforce",
            display_name="SF",
            entity_key=key,
            catalog_label=key,
            entity_label=key,
            resolution_source="goal_entity",
            confidence=0.95,
        )

    async def _run():
        with patch(
            "services.planning.entity_resolver._resolve_side_entity",
            side_effect=fake_side,
        ):
            return await resolve_plan_entities(
                {
                    "source": "salesforce",
                    "destination": "salesforce",
                    "source_name": "SF 1",
                    "destination_name": "SF 2",
                    "destination_object_api_name": "Account",
                },
                user_id="user-1",
                prompt="migrate contact details to account object in SF 2",
                parsed_goal={
                    "entities": ["contacts", "account"],
                    "destination_phrase": "account object",
                },
                migration_intent=intent,
            )

    contract = asyncio.run(_run())
    assert contract.source.entity_key == "Contact"
    assert contract.destination.entity_key == "Account"
    assert contract.migration_intent is not None


def test_resolve_plan_entities_infers_contact_from_driving_entity():
    from services.planning.entity_resolver import resolve_plan_entities
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.schemas import PlanEntityRef

    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        destination_entity_key="Contact",
        cross_system=True,
    )

    async def fake_side(*, user_id, classification, role, prompt, parsed_goal, migration_intent=None):
        del user_id, classification, prompt, parsed_goal
        mi = migration_intent or {}
        if role == "destination":
            key = mi.get("destination_entity_key") or "Wrong"
        else:
            key = mi.get("driving_entity_key") or "Wrong"
        return PlanEntityRef(
            connector_type="salesforce",
            display_name="SF",
            entity_key=key,
            catalog_label=key,
            entity_label=key,
            resolution_source="goal_entity",
            confidence=0.95,
        )

    async def _run():
        with patch(
            "services.planning.entity_resolver._resolve_side_entity",
            side_effect=fake_side,
        ):
            return await resolve_plan_entities(
                {
                    "source": "salesforce",
                    "destination": "salesforce",
                    "source_name": "SF 1",
                    "destination_name": "SF 2",
                },
                user_id="user-1",
                prompt="deploy contacts from SF 1 to SF 2",
                parsed_goal={"entities": ["contacts"]},
                migration_intent=intent,
            )

    contract = asyncio.run(_run())
    assert contract.source.entity_key == "Contact"
    assert contract.destination.entity_key == "Contact"
