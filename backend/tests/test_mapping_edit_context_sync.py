"""Tests for mapping edit sync to migration context."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.session import AgentSession
from agent.tools.map_migrate_tools import StartMigrationTool
from services.migration.context import MigrationContext
from services.migration.context_repository import patch_context


def test_patch_context_updates_transformation_rules():
    existing = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Email": {"table": "users", "column": "email"}},
        document_type="Contact",
        destination="sql",
        conversation_id="conv-1",
        mapping_id="map-1",
        mapping_version=1,
        transformation_rules={"fields": []},
    )

    async def run():
        with patch(
            "services.migration.context_repository.get_context",
            new_callable=AsyncMock,
            return_value=existing,
        ), patch(
            "services.migration.context_repository.upsert_context",
            new_callable=AsyncMock,
            side_effect=lambda ctx: ctx,
        ) as mock_upsert:
            rules = {"fields": [{"action": "split_2", "source_field": "Name"}]}
            updated = await patch_context(
                user_id="user-1",
                conversation_id="conv-1",
                extraction_job_id="job-1",
                mapping={
                    "Email": {
                        "table": "users",
                        "column": "email",
                        "lookup_of": "user_plans.user_id",
                    }
                },
                transformation_rules=rules,
                table_name="users",
                migration_rules={"available_tables": {"users": {}, "user_plans": {}}},
            )
        assert updated is not None
        saved = mock_upsert.call_args[0][0]
        assert saved.mapping["Email"]["lookup_of"] == "user_plans.user_id"
        assert saved.transformation_rules == rules
        assert saved.table_name == "users"
        assert "available_tables" in saved.migration_rules

    asyncio.run(run())


def test_resolve_prefers_db_context_over_session():
    from services.migration.session_migration import resolve_migration_context_for_tool

    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        active_job_id="job-1",
    )
    session.mapping_config = {"Phone": {"table": "users", "column": "phone_stale"}}
    session.destination = {
        "connector_type": "sql",
        "display_name": "Should not be used for type",
    }
    session.destination_name = "Stale DB name"

    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Phone": {"table": "users", "column": "phone_edited"}},
        document_type="Contact",
        destination="sql",
        destination_name="Test DB",
        conversation_id="conv-1",
        mapping_version=3,
    )

    async def run():
        with patch(
            "services.migration.session_migration.get_context",
            new_callable=AsyncMock,
            return_value=db_ctx,
        ), patch(
            "services.migration.session_migration.enrich_migration_context",
            new_callable=AsyncMock,
            side_effect=lambda ctx, _s, _j: ctx,
        ):
            ctx = await resolve_migration_context_for_tool(session, "job-1")
        assert ctx is not None
        assert ctx.mapping["Phone"]["column"] == "phone_edited"
        assert ctx.destination == "sql"
        assert ctx.destination_name == "Test DB"

    asyncio.run(run())


def test_flush_mapping_draft_to_context_updates_db():
    from services.conversation.actions.mapping_draft import flush_mapping_draft_to_context

    stale = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"A": {"table": "users", "column": "a"}},
        document_type="Contact",
        destination="sql",
        conversation_id="conv-1",
        mapping_id="map-1",
        mapping_version=1,
    )
    updated_mapping = {
        "A": {"table": "users", "column": "a"},
        "B": {"table": "users", "column": "b"},
    }
    conv_session = MagicMock(
        mapping_draft={"mapping_config": updated_mapping},
        active_job_id="job-1",
        latest_mapping_message_id="msg-1",
        intent_state={"active_destination": "sql", "destination_name": "Test DB"},
    )

    async def run():
        with patch(
            "services.conversation.actions.mapping_draft.load_or_hydrate_session",
            new_callable=AsyncMock,
            return_value=conv_session,
        ), patch(
            "services.conversation.actions.mapping_draft.flush_mapping_draft_to_chat",
            new_callable=AsyncMock,
            return_value={"metadata": {"mapping_id": "map-1", "document_type": "Contact"}},
        ), patch(
            "services.conversation.actions.mapping_draft._load_mapping_message_metadata",
            new_callable=AsyncMock,
            return_value={
                "mapping_id": "map-1",
                "document_type": "Contact",
                "destination": "sql",
                "destination_name": "Test DB",
            },
        ), patch(
            "services.conversation.actions.mapping_draft.get_context",
            new_callable=AsyncMock,
            return_value=stale,
        ), patch(
            "services.conversation.actions.mapping_draft.patch_context",
            new_callable=AsyncMock,
            side_effect=lambda **kwargs: MigrationContext(
                user_id="user-1",
                extraction_job_id="job-1",
                mapping=dict(kwargs["mapping"]),
                document_type="Contact",
                destination="sql",
                conversation_id="conv-1",
                mapping_version=2,
                operation="insert",
            ),
        ) as mock_patch, patch(
            "services.conversation.actions.mapping_draft.upsert_context",
            new_callable=AsyncMock,
            side_effect=lambda ctx: ctx,
        ):
            result = await flush_mapping_draft_to_context("user-1", "conv-1", job_id="job-1")
        assert result is not None
        assert result["mapping_field_count"] == 2
        mock_patch.assert_called_once()
        assert len(mock_patch.call_args.kwargs["mapping"]) == 2

    asyncio.run(run())


def test_start_migration_executes_on_backend():
    tool = StartMigrationTool()
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        active_job_id="job-1",
        destination="sql",
        destination_name="Test DB",
    )
    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Email": {"table": "users", "column": "email"}},
        document_type="Contact",
        destination="sql",
        conversation_id="conv-1",
        mapping_id="map-1",
        connector_id="conn-1",
        table_name="users",
    )
    start_result = MagicMock(
        job_id="mig-1",
        batch_id="batch-1",
        destination="sql",
        destination_name="Test DB",
        total_records=10,
        source_job_id="job-1",
        extra={},
    )

    async def run():
        with patch(
            "services.migration.session_migration.resolve_migration_context_for_execution",
            new_callable=AsyncMock,
            return_value=db_ctx,
        ), patch(
            "services.migration.migration_service.MigrationService.start",
            new_callable=AsyncMock,
            return_value=start_result,
        ), patch(
            "services.extraction_results_service.ExtractionResultsService.update_pipeline_stage",
            new_callable=AsyncMock,
        ), patch(
            "services.conversation.session_sync.sync_session_after_migration",
            new_callable=AsyncMock,
        ):
            result = await tool.run(session, {})
        assert result.ok is True
        assert result.user_visible.get("action") == "migration_started"
        assert result.user_visible.get("job_id") == "mig-1"

    asyncio.run(run())


def test_run_migrate_action_uses_execution_resolver():
    from services.conversation.actions.migrate import run_migrate_action

    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={
            "Email": {"table": "users", "column": "email", "is_duplicate_check": True},
            "Phone": {"table": "users", "column": "phone"},
        },
        document_type="Contact",
        destination="sql",
        conversation_id="conv-1",
        mapping_id="map-1",
    )
    start_result = MagicMock(
        job_id="mig-1",
        batch_id="batch-1",
        destination="sql",
        destination_name="Test DB",
        total_records=2,
        source_job_id="job-1",
    )
    conv_session = MagicMock(active_job_id="job-1", latest_mapping_message_id="msg-1")

    async def run():
        with patch(
            "services.conversation.session_store.load_or_hydrate_session",
            new_callable=AsyncMock,
            return_value=conv_session,
        ), patch(
            "services.migration.session_migration.resolve_migration_context_for_execution",
            new_callable=AsyncMock,
            return_value=db_ctx,
        ) as mock_resolve, patch(
            "services.migration.migration_service.MigrationService.start",
            new_callable=AsyncMock,
            return_value=start_result,
        ), patch(
            "services.conversation.session_sync.sync_session_after_migration",
            new_callable=AsyncMock,
        ):
            result = await run_migrate_action(
                user_id="user-1",
                conversation_id="conv-1",
                job_id="job-1",
            )
        assert result["status"] == "success"
        mock_resolve.assert_called_once()
        assert len(mock_resolve.call_args.kwargs["job_id"]) > 0

    asyncio.run(run())
