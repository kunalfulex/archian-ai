"""Chat load path — slim metadata and DB retry."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from db.retry import is_retryable_db_error, with_db_retry
from services.chat_service import (
    ChatService,
    _pick_latest_open_planner_message_id,
    _slim_message_metadata_for_api,
)


def test_is_retryable_db_error():
    assert is_retryable_db_error(Exception("connection was closed in the middle of operation"))
    assert not is_retryable_db_error(Exception("syntax error at line 1"))


def test_slim_metadata_strips_conversation_history():
    meta = {
        "event_type": "planner_plan_ready",
        "planner_pending": {
            "plan_id": "p1",
            "conversation_history": [{"type": "user", "text": "hi"}],
            "classification": {"intent": "extract", "_plan_requirements": {"x": 1}},
        },
    }
    out = _slim_message_metadata_for_api(meta, keep_full_planner_pending=True)
    assert "conversation_history" not in out["planner_pending"]
    assert "_plan_requirements" not in out["planner_pending"]["classification"]


def test_slim_metadata_resolved_planner_keeps_summary_only():
    meta = {
        "event_type": "planner_plan_ready",
        "planner_resolved": True,
        "planner_pending": {
            "plan_id": "p1",
            "original_prompt": "extract contacts",
            "classification": {"intent": "extract"},
            "execution_plan": {"status": "approved"},
        },
    }
    out = _slim_message_metadata_for_api(meta, keep_full_planner_pending=False)
    assert out["planner_pending"] == {
        "plan_id": "p1",
        "original_prompt": "extract contacts",
    }


def test_pick_latest_open_planner_message_id():
    messages = [
        {"id": "1", "metadata": {"event_type": "user"}},
        {
            "id": "2",
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {"plan_id": "a"},
                "plan_status": "awaiting_clarification",
            },
        },
    ]
    assert _pick_latest_open_planner_message_id(messages) == "2"


def test_get_messages_skips_duplicate_conversation_lookup():
    async def _run():
        records = [
            {
                "id": "m1",
                "conversation_id": "c1",
                "user_id": "u1",
                "role": "user",
                "content": "hello",
                "metadata": None,
                "created_at": None,
            }
        ]
        with patch.object(ChatService, "_ensure_tables_exist", AsyncMock()), patch.object(
            ChatService, "get_conversation", AsyncMock()
        ) as mock_get_conv, patch.object(
            ChatService, "_fetch_message_records", AsyncMock(return_value=records)
        ):
            out = await ChatService.get_messages(
                "c1",
                "u1",
                conversation_verified=True,
                slim=False,
            )
        mock_get_conv.assert_not_called()
        assert len(out) == 1
        assert out[0]["content"] == "hello"

    asyncio.run(_run())


def test_with_db_retry_recovers_once():
    async def _run():
        calls = {"n": 0}

        async def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("connection was closed in the middle of operation")
            return "ok"

        with patch("db.connection.database") as mock_db:
            mock_db.disconnect = AsyncMock()
            mock_db.connect = AsyncMock()
            result = await with_db_retry(flaky, attempts=2)
        assert result == "ok"
        assert calls["n"] == 2

    asyncio.run(_run())
