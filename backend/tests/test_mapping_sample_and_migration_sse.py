"""Mapping sample row selection and migration SSE helpers."""

import asyncio
from unittest.mock import AsyncMock, patch

from utils.mapping_sample import pick_most_complete_record
from utils.migration_sse_stream import (
    chat_message_sse_payload,
    migration_started_extra_only,
    migration_started_sse,
    stream_migration_terminal_chat_events,
)


def test_pick_most_complete_record_prefers_filled_row():
    rows = [
        {"email": "a@b.com", "first_name": "Sai", "last_name": "", "phone": "", "company": ""},
        {
            "email": "full@example.com",
            "first_name": "Kunal",
            "last_name": "Fule",
            "phone": "+1-555-0100",
            "company": "Acme",
        },
    ]
    best = pick_most_complete_record(rows)
    assert best["email"] == "full@example.com"
    assert best["phone"] == "+1-555-0100"
    assert best["company"] == "Acme"


def test_migration_started_sse_ignores_duplicate_extra_destination():
    line = migration_started_sse(
        job_id="m1",
        batch_id="b1",
        destination="google_sheets",
        destination_name="contact list",
        total_records=4,
        **migration_started_extra_only(
            {"destination": "google_sheets", "spreadsheet_id": "abc"}
        ),
    )
    assert '"destination": "google_sheets"' in line
    assert '"spreadsheet_id": "abc"' in line


def test_chat_message_sse_payload_shape():
    payload = chat_message_sse_payload(
        {
            "id": "msg-1",
            "content": "done",
            "created_at": "2026-06-01T00:00:00Z",
            "metadata": {"event_type": "migration_completed", "job_id": "m1"},
        }
    )
    assert payload["id"] == "msg-1"
    assert payload["metadata"]["job_id"] == "m1"


def test_stream_migration_terminal_chat_events_emits_completed_job_finished_and_recovery():
    completed = {
        "id": "msg-complete",
        "content": "Migration done",
        "created_at": "2026-06-01T00:00:00Z",
        "metadata": {
            "event_type": "migration_completed",
            "job_id": "m1",
            "batch_id": "b1",
            "error_count": 2,
        },
    }
    recovery = {
        "id": "msg-recovery",
        "content": "Fix errors",
        "created_at": "2026-06-01T00:00:01Z",
        "metadata": {
            "event_type": "planner_recovery_required",
            "recovery_proposal": {"requires_write": True},
            "planner_pending": {"recovery_batch_id": "b1"},
        },
    }
    job_data = {
        "status": "completed",
        "total_records": 10,
        "processed_records": 10,
        "successful_records": 8,
        "failed_records": 2,
        "batch_id": "b1",
    }

    async def _find_completed(conversation_id, user_id, event_type, *, job_id=None, batch_id=None):
        if event_type == "migration_completed":
            return completed
        return None

    async def _find_recovery(conversation_id, user_id, event_type, *, job_id=None, batch_id=None):
        if event_type == "planner_recovery_required":
            return recovery
        return None

    async def _empty_recovery_async_gen(**kwargs):
        for _ in ():
            yield {}

    async def _run():
        with patch(
            "utils.migration_sse_stream.wait_for_persisted_migration_completed",
            new=AsyncMock(return_value=completed),
        ), patch(
            "utils.migration_sse_stream.stream_migration_recovery_analysis",
            new=_empty_recovery_async_gen,
        ), patch(
            "utils.migration_sse_stream.wait_for_persisted_recovery_chat",
            new=AsyncMock(return_value=recovery),
        ):
            events = [
                evt
                async for evt in stream_migration_terminal_chat_events(
                    job_id="m1",
                    conversation_id="conv-1",
                    user_id="user-1",
                    batch_id="b1",
                    error_count=2,
                    job_data=job_data,
                )
            ]
        return events

    async def _empty_recovery_async_gen(**kwargs):
        for _ in ():
            yield {}

    events = asyncio.run(_run())
    assert [e["event_type"] for e in events] == [
        "migration_completed",
        "migration_job_finished",
        "recovery_analysis_started",
        "planner_recovery_required",
    ]
    assert events[0]["message"]["id"] == "msg-complete"
    assert events[1]["job_status"]["job_id"] == "m1"
    assert events[-1]["message"]["metadata"]["recovery_proposal"]["requires_write"] is True
