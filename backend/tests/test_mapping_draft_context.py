"""Tests for mapping draft persistence to conversation_migration_contexts."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.conversation.actions.mapping_draft import (
    flush_mapping_draft_to_context,
    patch_mapping_draft,
)
from services.migration.context import MigrationContext


def test_patch_mapping_draft_persists_to_db_context():
    draft = {
        "job_id": "job-map-1",
        "mapping_config": {
            "Email": {"table": "users", "column": "email", "is_duplicate_check": True},
            "Phone": {"table": "users", "column": "phone", "is_duplicate_check": False},
        },
        "destination": "sql",
        "destination_name": "Test DB",
        "table_name": "users",
    }
    saved = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-map-1",
        mapping=dict(draft["mapping_config"]),
        document_type="Contact",
        destination="sql",
        destination_name="Test DB",
        table_name="users",
        mapping_version=2,
    )

    async def run():
        session = MagicMock(active_job_id="job-map-1", latest_mapping_message_id="msg-1")
        with patch(
            "services.conversation.actions.mapping_draft.update_session",
            new=AsyncMock(return_value=session),
        ), patch(
            "services.conversation.actions.mapping_draft._load_mapping_message_metadata",
            new=AsyncMock(return_value={"destination": "sql"}),
        ), patch(
            "services.conversation.actions.mapping_draft._persist_draft_to_db_context",
            new=AsyncMock(return_value=saved),
        ) as persist:
            result = await patch_mapping_draft("user-1", "conv-1", draft)
        assert result["context_persisted"] is True
        assert result["context_job_id"] == "job-map-1"
        persist.assert_awaited_once()

    asyncio.run(run())


def test_flush_mapping_draft_uses_lineage_owner_when_child_has_no_row():
    draft = {
        "mapping_config": {
            "Email": {"table": "users", "column": "email", "is_duplicate_check": True},
        },
    }
    owner_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-v2",
        mapping={"Email": {"table": "users", "column": "email"}},
        document_type="Contact",
        destination="sql",
        destination_name="Test DB",
        table_name="users",
        mapping_version=3,
    )

    async def run():
        session = MagicMock(
            mapping_draft=draft,
            active_job_id="job-v3",
            latest_mapping_message_id="msg-1",
        )
        with patch(
            "services.conversation.actions.mapping_draft.load_or_hydrate_session",
            new=AsyncMock(return_value=session),
        ), patch(
            "services.conversation.actions.mapping_draft.flush_mapping_draft_to_chat",
            new=AsyncMock(return_value={"metadata": {"job_id": "job-v3"}}),
        ), patch(
            "services.conversation.actions.mapping_draft._load_mapping_message_metadata",
            new=AsyncMock(return_value={"destination": "sql", "destination_name": "Test DB"}),
        ), patch(
            "services.conversation.actions.mapping_draft._persist_draft_to_db_context",
            new=AsyncMock(return_value=owner_ctx),
        ) as persist:
            result = await flush_mapping_draft_to_context(
                "user-1",
                "conv-1",
                job_id="job-v3",
            )
        assert result is not None
        assert result["context_job_id"] == "job-v2"
        persist.assert_awaited_once()
        call_kwargs = persist.await_args.kwargs
        assert call_kwargs["target_job_id"] == "job-v3"

    asyncio.run(run())


def test_persist_draft_falls_back_to_lineage_owner():
    from services.conversation.actions.mapping_draft import _persist_draft_to_db_context

    draft = {
        "mapping_config": {
            "Email": {"table": "users", "column": "email", "is_duplicate_check": True},
        },
    }
    owner_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-v2",
        mapping={"Email": {"table": "users", "column": "email"}},
        destination="sql",
        destination_name="Test DB",
        document_type="Contact",
        table_name="users",
    )
    updated = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-v2",
        mapping=dict(draft["mapping_config"]),
        document_type="Contact",
        destination="sql",
        destination_name="Test DB",
        table_name="users",
        mapping_version=4,
    )

    async def run():
        session = MagicMock(intent_state={})
        with patch(
            "services.conversation.actions.mapping_draft._upsert_context_from_draft",
            new=AsyncMock(side_effect=[None, updated]),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=(owner_ctx, "job-v2")),
        ):
            saved = await _persist_draft_to_db_context(
                user_id="user-1",
                conversation_id="conv-1",
                draft=draft,
                target_job_id="job-v3",
                conv_session=session,
                meta={"destination": "sql", "destination_name": "Test DB"},
            )
        assert saved is not None
        assert saved.extraction_job_id == "job-v2"
        assert saved.mapping["Email"]["is_duplicate_check"] is True

    asyncio.run(run())
