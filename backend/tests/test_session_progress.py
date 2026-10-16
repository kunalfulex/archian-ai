"""Tests for pipeline progress summary used by S01 session continuation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.session_continuation import (
    _apply_progress_guardrails,
    resolve_session_continuation,
    SessionContinuationDecision,
)
from services.planning.conversation_session import build_session_progress_summary


def _job_history(*, with_map: bool = False, with_migrate: bool = False) -> list:
    history = [
        {
            "type": "system",
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-abc",
                "source_type": "sql",
                "source_name": "Test DB",
                "table_name": "users",
                "record_count": 4,
            },
        },
    ]
    if with_map:
        history.append(
            {
                "type": "assistant",
                "metadata": {
                    "event_type": "mapping_started",
                    "job_id": "job-abc",
                    "mapping_id": "map-1",
                    "destination": "salesforce",
                    "destination_name": "SF 1",
                    "destination_object_api_name": "Contact",
                    "mapping_config": {
                        "email": {"object": "Contact", "field": "Email"},
                        "first_name": {"object": "Contact", "field": "FirstName"},
                    },
                },
            }
        )
    if with_migrate:
        history.append(
            {
                "type": "system",
                "metadata": {
                    "event_type": "migration_completed",
                    "job_id": "job-abc",
                    "status": "success",
                    "success_count": 4,
                    "error_count": 0,
                },
            }
        )
    return history


def test_progress_extract_only():
    progress = build_session_progress_summary(_job_history())
    assert progress["last_completed_step"] == "extract"
    assert progress["next_logical_step"] == "map"
    assert progress["mapping_ready"] is False


def test_progress_after_mapping():
    progress = build_session_progress_summary(_job_history(with_map=True))
    assert progress["last_completed_step"] == "map"
    assert progress["next_logical_step"] == "migrate"
    assert progress["mapping_ready"] is True
    assert progress["steps"]["map"]["destination_object_api_name"] == "Contact"


def test_progress_after_migration():
    progress = build_session_progress_summary(_job_history(with_map=True, with_migrate=True))
    assert progress["last_completed_step"] == "migrate"
    assert progress["next_logical_step"] == "none"
    assert progress["pipeline_stage"] == "migrated"


def test_progress_guardrail_attaches_metadata_without_overriding_llm():
    """S01 LLM is authoritative; progress guardrails only enrich metadata."""
    progress = build_session_progress_summary(_job_history(with_map=True))
    llm_decision = SessionContinuationDecision(
        session_action="fresh_full_pipeline",
        continuation_phase="none",
        reuse_job_id=False,
        job_id="job-abc",
        confidence=0.9,
        decision_source="llm",
    )
    out = _apply_progress_guardrails(llm_decision, progress)
    assert out.session_action == "fresh_full_pipeline"
    assert out.continuation_phase == "none"
    assert out.reuse_job_id is False
    assert out.decision_source == "llm"
    assert out.last_completed_step == "map"


def test_resolve_start_migration_after_map_trusts_llm_decision():
    llm_json = {
        "session_action": "continue_active_job",
        "continuation_phase": "migrate",
        "reuse_job_id": True,
        "confidence": 0.9,
        "reasoning_short": "User wants to migrate mapped data",
    }
    with patch(
        "services.architect_agent.async_openai_call",
        new_callable=AsyncMock,
    ) as mock_call:
        mock_call.return_value.choices = [
            type(
                "C",
                (),
                {
                    "message": type(
                        "M",
                        (),
                        {"content": __import__("json").dumps(llm_json)},
                    )()
                },
            )()
        ]
        decision = asyncio.run(
            resolve_session_continuation(
                "start the migration",
                user_id="u1",
                conversation_history=_job_history(with_map=True),
            )
        )

    assert decision.session_action == "continue_active_job"
    assert decision.continuation_phase == "migrate"
    assert decision.reuse_job_id is True
    assert decision.job_id == "job-abc"
    assert decision.decision_source == "llm"
    assert decision.last_completed_step == "map"
    assert decision.session_progress["mapping_ready"] is True
