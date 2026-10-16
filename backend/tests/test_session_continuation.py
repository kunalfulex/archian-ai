"""Tests for S01 session continuation LLM decision."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.session_continuation import (
    SessionContinuationDecision,
    _apply_guardrails,
    _apply_progress_guardrails,
    _rules_fallback_decision,
    merge_session_decision_into_classification,
    resolve_session_continuation,
    session_action_is_fresh,
)


def test_session_action_is_fresh():
    assert session_action_is_fresh("fresh_extract")
    assert session_action_is_fresh("fresh_full_pipeline")
    assert not session_action_is_fresh("continue_active_job")
    assert not session_action_is_fresh("no_prior_job")


def test_no_job_skips_llm():
    decision = asyncio.run(
        resolve_session_continuation(
            "map to Contact",
            user_id="u1",
            conversation_history=[],
        )
    )
    assert decision.session_action == "no_prior_job"
    assert decision.decision_source == "no_job"
    assert not decision.reuse_job_id


def test_rules_fallback_uses_job_progress():
    job = {
        "job_id": "job-1",
        "source_type": "sql",
        "source_name": "Test DB",
        "state": "extraction_completed",
    }
    progress = {
        "job_id": "job-1",
        "last_completed_step": "extract",
        "next_logical_step": "map",
        "mapping_ready": False,
    }
    decision = _rules_fallback_decision("map the data to SF 1 Contact object", job, progress)
    assert decision.session_action == "continue_active_job"
    assert decision.continuation_phase == "none"
    assert decision.reuse_job_id is True
    assert decision.job_id == "job-1"


def test_rules_fallback_no_job():
    decision = _rules_fallback_decision("extract contacts", None, None)
    assert decision.session_action == "no_prior_job"


def test_llm_continue_active_job():
    job_history = [
        {
            "type": "system",
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-abc",
                "source_type": "sql",
                "source_name": "Test DB",
                "record_count": 4,
            },
        }
    ]
    llm_json = {
        "session_action": "continue_active_job",
        "continuation_phase": "map",
        "reuse_job_id": True,
        "confidence": 0.93,
        "reasoning_short": "User wants to map prior extraction",
    }

    with patch(
        "services.architect_agent.async_openai_call",
        new_callable=AsyncMock,
    ) as mock_call:
        mock_call.return_value.choices = [
            type("C", (), {"message": type("M", (), {"content": __import__("json").dumps(llm_json)})()})()
        ]
        decision = asyncio.run(
            resolve_session_continuation(
                "please wire these rows to SF 1 Contact",
                user_id="u1",
                conversation_history=job_history,
            )
        )

    assert decision.session_action == "continue_active_job"
    assert decision.reuse_job_id is True
    assert decision.job_id == "job-abc"
    assert decision.decision_source == "llm"


def test_merge_sets_active_job_and_session_action():
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="map",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    cls = merge_session_decision_into_classification(
        {"intent": "map_data", "mode": "map", "operations": ["map"]},
        decision,
    )
    assert cls["active_job_id"] == "job-1"
    assert cls["session_action"] == "continue_active_job"
    assert cls["continuation_phase"] == "map"
    assert "pipeline_action" not in cls


def test_guardrail_low_confidence_forces_fresh():
    job = {"job_id": "job-1"}
    raw = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="map",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.2,
        decision_source="llm",
    )
    out = _apply_guardrails(raw, job)
    assert out.session_action == "fresh_extract"
    assert not out.reuse_job_id


def test_apply_guardrails_does_not_override_llm_migrate_phase_for_map_prompt():
    job = {"job_id": "job-1"}
    progress = {
        "job_id": "job-1",
        "last_completed_step": "map",
        "next_logical_step": "migrate",
        "mapping_ready": True,
    }
    raw = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="migrate",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    out = _apply_guardrails(raw, job, prompt="map to sf 2", progress_summary=progress)
    assert out.continuation_phase == "migrate"
    assert out.decision_source == "llm"


def test_progress_guardrail_preserves_llm_phase():
    progress = {
        "job_id": "job-1",
        "last_completed_step": "extract",
        "next_logical_step": "map",
        "mapping_ready": False,
    }
    raw = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="data_ops",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    out = _apply_progress_guardrails(
        raw,
        progress,
        prompt="remove any invalid email id records",
    )
    assert out.continuation_phase == "data_ops"
    assert out.decision_source == "llm"


def test_apply_guardrails_keeps_migrate_for_start_migration():
    job = {"job_id": "job-1"}
    progress = {
        "job_id": "job-1",
        "last_completed_step": "map",
        "next_logical_step": "migrate",
        "mapping_ready": True,
    }
    raw = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="migrate",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    out = _apply_guardrails(raw, job, prompt="start the migration", progress_summary=progress)
    assert out.continuation_phase == "migrate"
