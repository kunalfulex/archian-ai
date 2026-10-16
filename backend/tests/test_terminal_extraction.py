"""Tests for terminal extraction_job_finished gating and session job id."""
from __future__ import annotations

from agent.session import AgentSession
from agent.terminal_extraction import (
    is_migration_terminal_result,
    should_emit_terminal_extraction_finished,
)


def _session(**kwargs) -> AgentSession:
    return AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode=kwargs.pop("mode", "data_ops"),
        **kwargs,
    )


def test_is_migration_terminal_result_from_action():
    assert is_migration_terminal_result({"action": "migration_started", "job_id": "bg-1"})
    assert is_migration_terminal_result({"action": "migration_needs_mapping"})
    assert not is_migration_terminal_result(
        {"action": "map_fields_ready", "job_id": "extract-1"}
    )


def test_is_migration_terminal_result_from_batch_id():
    assert is_migration_terminal_result(
        {"batch_id": "b1", "job_id": "bg-1", "source_job_id": "extract-1"}
    )
    assert not is_migration_terminal_result(
        {
            "batch_id": "b1",
            "job_id": "extract-2",
            "operations_applied": ["filter"],
        }
    )


def test_should_not_emit_after_migration():
    session = _session(active_job_id="extract-1")
    session.last_result = {
        "action": "migration_started",
        "job_id": "bg-1",
        "batch_id": "batch-1",
        "source_job_id": "extract-1",
        "record_count": 157,
    }
    assert should_emit_terminal_extraction_finished(session) is False


def test_should_emit_after_data_ops():
    session = _session(active_job_id="extract-2")
    session.last_result = {
        "job_id": "extract-2",
        "record_count": 158,
        "operations_applied": ["filter"],
        "extraction_version": 2,
        "parent_job_id": "extract-1",
    }
    assert should_emit_terminal_extraction_finished(session) is True


def test_should_not_emit_after_map_fields_ready():
    session = _session(active_job_id="extract-2")
    session.last_result = {
        "action": "map_fields_ready",
        "job_id": "extract-2",
    }
    assert should_emit_terminal_extraction_finished(session) is False


def test_merge_result_keeps_extraction_job_on_migration():
    session = _session(active_job_id="extract-2")
    session.merge_result(
        {
            "action": "migration_started",
            "job_id": "bg-1",
            "batch_id": "batch-1",
            "source_job_id": "extract-2",
        }
    )
    assert session.active_job_id == "extract-2"
    assert session.last_result["action"] == "migration_started"


def test_merge_result_updates_extraction_job_after_data_ops():
    session = _session(active_job_id="extract-1")
    session.merge_result(
        {
            "job_id": "extract-2",
            "record_count": 158,
            "operations_applied": ["filter"],
            "extraction_version": 2,
        }
    )
    assert session.active_job_id == "extract-2"
