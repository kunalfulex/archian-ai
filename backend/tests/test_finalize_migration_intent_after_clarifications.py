"""Tests for post-clarification migration intent finalization (no keyword ground)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.migration_intent import finalize_migration_intent_after_clarifications
from services.planning.migration_intent_schemas import EntityMention, PromptMigrationIntent


def test_finalize_related_extract_after_clarification():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        driving_entity_label="Contact",
        include_related_data=True,
        relationship_status="related",
        related_entity_phrase="account",
        cross_system=True,
        entity_mentions=[
            EntityMention(phrase="contacts", role="extract_primary"),
            EntityMention(phrase="account", role="ambiguous", confidence=0.5),
        ],
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "source_connector_id": "tok-1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "source_object_api_name": "Contact",
    }

    async def _run():
        with patch(
            "services.planning.migration_intent._load_destination_catalog_summary",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.planning.catalog_grounding.load_side_catalog_summary",
            new=AsyncMock(
                return_value=(
                    [
                        {"api_name": "Contact", "label": "Contact"},
                        {"api_name": "Account", "label": "Account"},
                    ],
                    "salesforce",
                    {"org_id": "org-1"},
                )
            ),
        ), patch(
            "utils.destination_connector_resolve.resolve_salesforce_token_record",
            new=AsyncMock(return_value={"org_id": "org-1", "id": "tok-1"}),
        ), patch(
            "services.planning.catalog_grounding.lookup_source_relationship",
            new=AsyncMock(return_value=("Contact", "Account", "AccountId", "Account")),
        ), patch(
            "services.planning.extract_field_clarification.finalize_extract_field_clarification",
            new=AsyncMock(side_effect=lambda intent, **kwargs: intent),
        ):
            return await finalize_migration_intent_after_clarifications(
                intent,
                user_id="user-1",
                classification=classification,
                prompt="extract contacts with account name from SF 1",
            )

    result = asyncio.run(_run())
    assert result.include_related_data is True
    assert result.parent_entity_key == "Account"
    assert result.lookup_field_path == "AccountId"


def test_finalize_skips_related_when_separate_pipelines():
    intent = PromptMigrationIntent(
        driving_entity_key="Contact",
        include_related_data=False,
        needs_separate_pipelines=True,
        extract_shape="multi_entity",
    )

    async def _run():
        with patch(
            "services.planning.migration_intent._load_destination_catalog_summary",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.planning.extract_field_clarification.finalize_extract_field_clarification",
            new=AsyncMock(side_effect=lambda intent, **kwargs: intent),
        ):
            return await finalize_migration_intent_after_clarifications(
                intent,
                user_id="user-1",
                classification={"source": "salesforce", "source_object_api_name": "Contact"},
                prompt="migrate contacts and accounts separately",
            )

    result = asyncio.run(_run())
    assert result.needs_separate_pipelines is True
    assert result.parent_entity_key is None
