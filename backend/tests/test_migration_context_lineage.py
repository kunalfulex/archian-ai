"""Migration context resolve with lineage inheritance."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.session import AgentSession
from services.migration.context import MigrationContext
from services.migration.session_migration import resolve_migration_context_for_tool


def test_resolve_uses_target_job_id_with_lineage_mapping():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        destination="salesforce",
        destination_name="Prod Org",
    )
    parent_mapping = {"Email": {"object": "Contact", "field": "Email"}}
    stored_parent = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-v2",
        mapping=parent_mapping,
        document_type="Contact",
        destination="salesforce",
        destination_name="Prod Org",
        mapping_id="map-parent",
    )

    async def run():
        with patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(stored_parent, "job-v2")),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "sql",
                    "source_identifier": "conn-1",
                    "extracted_data": [{"Email": "a@b.com"}, {"Email": "c@d.com"}],
                }
            ),
        ), patch(
            "services.migration.session_migration._lookup_salesforce_token_id",
            new=AsyncMock(return_value="token-1"),
        ):
            ctx = await resolve_migration_context_for_tool(session, "job-v3")
        assert ctx is not None
        assert ctx.extraction_job_id == "job-v3"
        assert ctx.mapping == parent_mapping
        assert ctx.mapping_id == "map-parent"

    asyncio.run(run())


def test_resolve_chat_fallback_allows_lineage_ancestor():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        destination="salesforce",
        conversation_history=[
            {
                "metadata": {
                    "event_type": "mapping_started",
                    "job_id": "job-v2",
                    "mapping_config": {"Name": {"object": "Account", "field": "Name"}},
                    "destination_name": "Prod Org",
                }
            }
        ],
    )

    async def run():
        with patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.mapping_lineage.is_lineage_ancestor",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration._lookup_salesforce_token_id",
            new=AsyncMock(return_value=None),
        ):
            ctx = await resolve_migration_context_for_tool(session, "job-v3")
        assert ctx is not None
        assert ctx.extraction_job_id == "job-v3"
        assert ctx.mapping.get("Name", {}).get("field") == "Name"

    asyncio.run(run())
