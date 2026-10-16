"""Tests for save-flow job resolution and action entry."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.conversation.actions.save_flow import run_save_flow_action
from services.flow.save_context import resolve_save_flow_records_job_id


def test_resolve_save_flow_records_job_id_from_batch_id():
    history = [
        {
            "metadata": {
                "event_type": "migration_completed",
                "batch_id": "batch-abc",
                "source_job_id": "job-child-9ce11f09",
                "destination_connector_id": "conn-1",
            }
        }
    ]

    async def run():
        with patch(
            "services.chat_service.ChatService.get_messages_with_metadata_for_context",
            new_callable=AsyncMock,
            return_value=history,
        ):
            job_id = await resolve_save_flow_records_job_id(
                user_id="user-1",
                conversation_id="conv-1",
                batch_id="batch-abc",
                session_active_job_id=None,
            )
        assert job_id == "job-child-9ce11f09"

    asyncio.run(run())


def test_resolve_save_flow_records_job_id_prefers_explicit_job_id():
    async def run():
        job_id = await resolve_save_flow_records_job_id(
            user_id="user-1",
            conversation_id="conv-1",
            job_id="explicit-job",
            batch_id="batch-abc",
            session_active_job_id="session-job",
        )
        assert job_id == "explicit-job"

    asyncio.run(run())


def test_resolve_save_flow_records_job_id_from_session_when_no_batch():
    async def run():
        job_id = await resolve_save_flow_records_job_id(
            user_id="user-1",
            conversation_id="conv-1",
            session_active_job_id="session-job",
        )
        assert job_id == "session-job"

    asyncio.run(run())


def test_run_save_flow_action_resolves_job_from_batch_when_session_empty():
    save_ctx = MagicMock()
    save_ctx.to_save_request.return_value = MagicMock()
    save_ctx.extracted_schema_type = "salesforce"
    save_ctx.source_name = "SF 1"
    save_ctx.destination = "sql"
    save_ctx.destination_name = "Test DB"
    save_ctx.records_job_id = "job-child-9ce11f09"

    empty_session = MagicMock(active_job_id=None, latest_mapping_message_id="msg-1", mapping_draft=None)

    async def run():
        with patch(
            "services.conversation.session_store.load_or_hydrate_session",
            new_callable=AsyncMock,
            return_value=empty_session,
        ), patch(
            "services.conversation.session_store.update_session",
            new_callable=AsyncMock,
        ) as mock_update, patch(
            "services.flow.save_context.resolve_save_flow_records_job_id",
            new_callable=AsyncMock,
            return_value="job-child-9ce11f09",
        ), patch(
            "services.conversation.actions.mapping_draft.flush_mapping_draft_to_context",
            new_callable=AsyncMock,
        ), patch(
            "services.flow.save_context.resolve_flow_save_context",
            new_callable=AsyncMock,
            return_value=save_ctx,
            ), patch(
                "services.conversation.actions.save_flow.save_mapping_flow",
                new_callable=AsyncMock,
                return_value={"flow_id": "flow-123"},
            ), patch(
                "services.chat_service.ChatService.log_data_event",
                new_callable=AsyncMock,
            ):
                result = await run_save_flow_action(
                    user_id="8eec776f-8caa-423e-b423-0bba9c0a3b76",
                    conversation_id="f8f0f4fc-8773-4c85-9a82-eccf76ae9d05",
                    flow_name="SF DB Flow",
                    batch_id="batch-abc",
                )
        assert result["status"] == "success"
        assert result["flow_id"] == "flow-123"
        mock_update.assert_called_once()
        assert mock_update.call_args.kwargs.get("active_job_id") == "job-child-9ce11f09"

    asyncio.run(run())
