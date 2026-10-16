"""Tests for failure-aware planning (Agent Runtime v2 Phase 4)."""
from __future__ import annotations

from services.planning.continuation_policy import (
    _session_signals_migration_retry,
    apply_migration_failure_continuation,
    build_retry_mapping_context,
    latest_partial_migration_metadata,
)
from services.planning.goal_parser import parse_goal


def test_session_signals_migration_retry():
    assert _session_signals_migration_retry(
        {"session_action": "continue_active_job", "continuation_phase": "migrate"}
    )
    assert _session_signals_migration_retry(
        {"session_action": "continue_active_job", "active_job_id": "job-1"}
    )
    assert not _session_signals_migration_retry({"session_action": "fresh_extract"})


def test_latest_partial_migration_metadata():
    history = [
        {
            "metadata": {
                "event_type": "migration_completed",
                "success_count": 70,
                "error_count": 30,
                "source_job_id": "job-abc",
            }
        }
    ]
    meta = latest_partial_migration_metadata(history)
    assert meta is not None
    assert meta["source_job_id"] == "job-abc"


def test_apply_migration_failure_continuation_pins_job():
    history = [
        {
            "metadata": {
                "event_type": "migration_completed",
                "success_count": 80,
                "error_count": 20,
                "source_job_id": "extract-1",
                "destination": "salesforce",
                "destination_name": "Prod Org",
            }
        }
    ]
    out = apply_migration_failure_continuation(
        {
            "mode": "extract",
            "session_action": "continue_active_job",
            "continuation_phase": "migrate",
        },
        prompt="fix migration errors and remap fields",
        conversation_history=history,
    )
    assert out.get("retry_after_partial") is True
    assert out.get("active_job_id") == "extract-1"


def test_build_retry_mapping_context():
    ctx = build_retry_mapping_context(
        {"failed_fields": ["LastName"], "top_errors": [{"message": "Required field missing"}]}
    )
    assert "LastName" in ctx


def test_parse_goal_migrate_defaults():
    goal = parse_goal({"intent": "migrate_data", "mode": "migrate"})
    assert "migrate" in goal["operations"] or "extract" in goal["operations"]
