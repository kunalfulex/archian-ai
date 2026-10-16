"""ConversationSession Redis store (with in-memory fallback)."""
from __future__ import annotations

import asyncio

import pytest

from services.conversation.session_store import (
    ConversationSession,
    hydrate_from_db,
    load_or_hydrate_session,
    load_session,
    update_session,
    _fallback_store,
)


@pytest.fixture(autouse=True)
def clear_fallback():
    _fallback_store.clear()
    yield
    _fallback_store.clear()


def test_update_and_load_session():
    async def _run():
        session = await update_session(
            "user-1",
            "conv-1",
            active_job_id="job-abc",
            intent_state={"active_source": "file", "state": "extraction_completed"},
            pipeline_progress={"job_id": "job-abc", "last_completed_step": "extract"},
        )
        assert session.active_job_id == "job-abc"
        loaded = await load_session("user-1", "conv-1")
        assert loaded is not None
        assert loaded.intent_state["active_source"] == "file"

    asyncio.run(_run())


def test_hydrate_from_empty_db(monkeypatch):
    async def fake_messages(*_a, **_k):
        return []

    monkeypatch.setattr(
        "services.chat_service.ChatService.get_messages_with_metadata_for_context",
        fake_messages,
    )

    async def _run():
        session = await hydrate_from_db("user-1", "conv-2")
        assert isinstance(session, ConversationSession)
        assert session.active_job_id is None

    asyncio.run(_run())


def test_load_or_hydrate_uses_cache(monkeypatch):
    async def _run():
        await update_session("user-1", "conv-3", active_job_id="cached-job")
        calls = []

        async def fake_hydrate(*_a, **_k):
            calls.append(1)
            return ConversationSession(user_id="user-1", conversation_id="conv-3")

        monkeypatch.setattr(
            "services.conversation.session_store.hydrate_from_db",
            fake_hydrate,
        )
        session = await load_or_hydrate_session("user-1", "conv-3")
        assert session.active_job_id == "cached-job"
        assert calls == []

    asyncio.run(_run())
