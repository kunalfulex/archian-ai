"""Tests for Agent Runtime v4.2 backlog: retrieval policy, quotas, tool audit."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent.turn_quotas import (
    TurnUsage,
    get_turn_usage,
    llm_pick_allowed,
    record_llm_pick,
    record_tool_invoke,
    save_turn_usage,
    tool_invoke_allowed,
)
from services.pipeline_learning.chunk_builder import build_platform_tool_pattern_text
from services.pipeline_learning.trace_retrieval_policy import (
    include_user_retrieval,
    platform_retrieval_enabled,
    trace_retrieval_policy,
)


def test_build_platform_tool_pattern_text_anonymized():
    text = build_platform_tool_pattern_text(
        steps=[
            {"tool_id": "extract_google_sheets", "ok": True},
            {"tool_id": "map_fields", "ok": True},
        ],
        source_type="google_sheets",
        destination_type="salesforce",
        mode="migration",
    )
    assert "trace_type:platform_tool_pattern" in text
    assert "goal:" not in text
    assert "job_id:" not in text
    assert "extract_google_sheets -> map_fields" in text


def test_trace_retrieval_policy_env(monkeypatch):
    monkeypatch.setenv("AGENT_TRACE_RETRIEVAL_POLICY", "user_only")
    assert trace_retrieval_policy() == "user_only"
    assert include_user_retrieval() is True
    assert platform_retrieval_enabled() is False

    monkeypatch.setenv("AGENT_TRACE_RETRIEVAL_POLICY", "platform_only")
    assert include_user_retrieval() is False
    assert platform_retrieval_enabled() is True

    monkeypatch.delenv("AGENT_TRACE_RETRIEVAL_POLICY", raising=False)
    assert trace_retrieval_policy() == "user_then_platform"


def test_turn_quotas_session_roundtrip():
    session = MagicMock()
    session.classification_snapshot = {}
    save_turn_usage(session, TurnUsage())
    assert get_turn_usage(session).llm_picks == 0

    record_llm_pick(session)
    assert get_turn_usage(session).llm_picks == 1

    record_tool_invoke(session)
    assert get_turn_usage(session).tool_invokes == 1


def test_llm_pick_quota_blocks(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_LLM_PICKS_PER_TURN", "1")
    session = MagicMock()
    session.classification_snapshot = {}
    save_turn_usage(session, TurnUsage(llm_picks=1))
    allowed, msg = llm_pick_allowed(session)
    assert allowed is False
    assert "quota" in msg.lower()


def test_tool_invoke_quota_blocks(monkeypatch):
    monkeypatch.setenv("AGENT_MAX_TOOL_INVOKES_PER_TURN", "2")
    session = MagicMock()
    session.classification_snapshot = {}
    save_turn_usage(session, TurnUsage(tool_invokes=2))
    allowed, msg = tool_invoke_allowed(session)
    assert allowed is False


def test_tool_usage_audit_shape():
    import asyncio

    from services.pipeline_learning.metrics import LearningMetricsService

    async def _run():
        with patch("services.pipeline_learning.metrics.database.fetch_all", new_callable=AsyncMock) as fetch:
            fetch.return_value = [
                {
                    "chunk_type": "tool_pick",
                    "metadata": {"actual_tool_ids": ["map_fields", "start_migration"]},
                },
                {
                    "chunk_type": "tool_trace",
                    "metadata": {"tool_ids": ["map_fields"]},
                },
            ]
            with patch(
                "services.pipeline_learning.metrics.PipelineLearningStore.ensure_tables",
                new_callable=AsyncMock,
            ):
                data = await LearningMetricsService.tool_usage_audit(days=30)
        assert "registered_count" in data
        assert "never_picked" in data
        assert "deprecation_candidates" in data
        assert data["registered_count"] > 0

    asyncio.run(_run())


def test_redact_platform_chunk():
    from services.pipeline_learning.retrieve import _redact_platform_chunk

    row = {
        "id": "abc",
        "user_id": "user-1",
        "content_text": "goal:secret\nstep1:map_fields",
        "metadata": {"job_id": "j1", "prompt": "hi", "source_type": "gsheets"},
    }
    out = _redact_platform_chunk(row)
    assert "user_id" not in out
    assert "goal:secret" not in out["content_text"]
    assert out["metadata"].get("anonymized") is True
