"""Tests for execution observation hook (Agent Runtime v2 Phase 2)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.observe import observation_chat_metadata, observe_step_result
from agent.observation import ExecutionObservation, FailureReport, TopError
from agent.recovery_policy import should_recover
from agent.session import AgentSession
from agent.tools.base import ToolResult


def test_observation_chat_metadata_includes_failure_report():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        batch_id="batch-1",
        failure_report=FailureReport(
            success_rate=0.7,
            failed_count=30,
            total_records=100,
            successful_records=70,
            failed_fields=["Email"],
            top_errors=[TopError(message="Invalid email", count=10)],
        ),
    )
    meta = observation_chat_metadata(obs)
    assert meta["tool_id"] == "start_migration"
    assert meta["failure_report"]["success_rate"] == 0.7
    assert "Email" in meta["failure_report"]["failed_fields"]


def test_maybe_offer_recovery_from_session_skips_background_migration():
    from agent.recovery_flow import maybe_offer_recovery_from_session

    session = AgentSession(user_id="u1", conversation_id="c1", mode="migrate")
    session.execution_trace.append(
        {
            "tool_id": "start_migration",
            "status": "submitted",
            "batch_id": "batch-1",
            "job_id": "job-1",
        }
    )

    async def _run():
        return await maybe_offer_recovery_from_session(session)

    assert asyncio.run(_run()) is None


def test_should_recover_from_observation_metadata():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        failure_report=FailureReport(
            success_rate=0.88,
            failed_count=12,
            successful_records=88,
            total_records=100,
        ),
    )
    assert should_recover(obs) is True


def test_observe_start_migration_background_is_submitted_not_zero_percent():
    session = AgentSession(user_id="u1", conversation_id="c1", mode="migrate")

    async def _run():
        return await observe_step_result(
            session,
            "start_migration",
            ToolResult(
                ok=True,
                background_job_id="job-1",
                user_visible={
                    "action": "migration_started",
                    "batch_id": "batch-1",
                    "job_id": "job-1",
                    "total_records": 10,
                },
            ),
        )

    obs = asyncio.run(_run())
    assert obs.status == "submitted"
    assert obs.failure_report is None
    assert should_recover(obs) is False


def test_build_observation_in_progress_when_no_records_processed_yet():
    from agent.observe import build_observation_from_batch

    async def _run():
        with patch(
            "services.migration_records_service.MigrationRecordsService.summarize_batch_failures",
            new=AsyncMock(
                return_value={
                    "batch_id": "batch-1",
                    "batch_status": "in_progress",
                    "ready_for_recovery": False,
                    "total_records": 10,
                    "successful_records": 0,
                    "failed_records": 0,
                    "success_rate": 0.0,
                }
            ),
        ):
            return await build_observation_from_batch(
                tool_id="start_migration",
                batch_id="batch-1",
            )

    obs = asyncio.run(_run())
    assert obs.status == "submitted"
    assert obs.failure_report is None
    assert obs.metrics.get("batch_status") == "in_progress"
    assert should_recover(obs) is False


def test_build_observation_completed_all_success_no_recovery():
    from agent.observe import build_observation_from_batch

    async def _run():
        with patch(
            "services.migration_records_service.MigrationRecordsService.summarize_batch_failures",
            new=AsyncMock(
                return_value={
                    "batch_id": "batch-1",
                    "batch_status": "completed",
                    "ready_for_recovery": True,
                    "total_records": 10,
                    "successful_records": 10,
                    "failed_records": 0,
                    "success_rate": 1.0,
                    "top_errors": [],
                    "failed_fields": [],
                    "error_samples": [],
                }
            ),
        ):
            return await build_observation_from_batch(
                tool_id="start_migration",
                batch_id="batch-1",
            )

    obs = asyncio.run(_run())
    assert obs.status == "success"
    assert should_recover(obs) is False


def test_build_observation_completed_with_failures_triggers_recovery():
    from agent.observe import build_observation_from_batch

    async def _run():
        with patch(
            "services.migration_records_service.MigrationRecordsService.summarize_batch_failures",
            new=AsyncMock(
                return_value={
                    "batch_id": "batch-1",
                    "batch_status": "completed",
                    "ready_for_recovery": True,
                    "total_records": 10,
                    "successful_records": 7,
                    "failed_records": 3,
                    "success_rate": 0.7,
                    "top_errors": [{"message": "Required field missing", "count": 3}],
                    "failed_fields": ["LastName"],
                    "error_samples": ["Required field missing: LastName"],
                }
            ),
        ):
            return await build_observation_from_batch(
                tool_id="start_migration",
                batch_id="batch-1",
            )

    obs = asyncio.run(_run())
    assert obs.status == "partial"
    assert obs.metrics.get("batch_status") == "completed"
    assert should_recover(obs) is True
