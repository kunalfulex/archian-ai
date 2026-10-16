"""Shared routing dialogue context for Gate 0 / S01 / PLAN_SCOPE."""
from __future__ import annotations

from services.intent.routing_context import (
    build_routing_dialogue_context,
    build_session_routing_block,
    recent_user_turns,
    session_action_is_continue,
)
from services.intent.session_continuation import SessionContinuationDecision


def test_recent_user_turns_excludes_current_prompt():
    history = [
        {"type": "user", "text": "create a google sheet"},
        {"type": "user", "text": "just create the sheet"},
    ]
    turns = recent_user_turns(
        history, current_prompt="just create the sheet", max_turns=5
    )
    assert turns == ["create a google sheet"]


def test_build_routing_dialogue_context_includes_extraction_event():
    history = [
        {"type": "user", "text": "extract contacts from SF 1"},
        {
            "type": "system",
            "metadata": {
                "event_type": "extraction_completed",
                "record_count": 42,
                "source_name": "SF 1",
                "object_api_name": "Contact",
            },
        },
        {"type": "user", "text": "remove invalid email records"},
    ]
    ctx = build_routing_dialogue_context(
        history, current_prompt="remove invalid email records"
    )
    assert "User: extract contacts from SF 1" in ctx
    assert "Extraction completed" in ctx
    assert "42 records" in ctx
    assert "remove invalid email records" not in ctx


def test_session_action_is_continue():
    assert session_action_is_continue(
        SessionContinuationDecision(
            session_action="continue_active_job",
            reuse_job_id=True,
            job_id="j1",
        )
    )
    assert not session_action_is_continue(
        SessionContinuationDecision(
            session_action="no_prior_job",
            reuse_job_id=False,
        )
    )


def test_build_session_routing_block_fresh_principle():
    history = [{"type": "user", "text": "create a google sheet"}]
    block = build_session_routing_block(
        history,
        current_prompt="create a google sheet",
        session_decision=SessionContinuationDecision(
            session_action="no_prior_job",
            continuation_phase="none",
            reuse_job_id=False,
            confidence=0.9,
            decision_source="llm",
        ),
    )
    assert "no_prior_job" in block
    assert "New pipeline task" in block
