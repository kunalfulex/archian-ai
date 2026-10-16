"""Tests for migration failure summary and recovery policy gates."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.observation import ExecutionObservation, FailureReport
from agent.recovery_policy import (
    should_offer_failure_summary,
    should_offer_write_recovery,
    should_recover,
)


def test_should_recover_false_when_high_success_rate():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="success",
        failure_report=FailureReport(
            success_rate=0.988,
            failed_count=2,
            successful_records=165,
            total_records=167,
        ),
    )
    assert should_recover(obs) is False


def test_should_offer_failure_summary_true_despite_high_success_rate():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="success",
        failure_report=FailureReport(
            success_rate=0.988,
            failed_count=2,
            successful_records=165,
            total_records=167,
        ),
        metrics={"batch_status": "completed", "success_rate": 0.988},
    )
    assert should_offer_failure_summary(obs) is True


def test_should_offer_write_recovery_requires_recoverable_rows():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        failure_report=FailureReport(
            success_rate=0.988,
            failed_count=2,
            successful_records=165,
            total_records=167,
        ),
        metrics={"batch_status": "completed"},
    )
    assert should_offer_write_recovery(obs, recoverable_count=0) is False
    assert should_offer_write_recovery(obs, recoverable_count=2) is True


def test_build_failure_summary_for_batch():
    from services.migration.failure_summary import build_failure_summary_for_batch

    async def _run():
        with patch(
            "services.migration_records_service.MigrationRecordsService.summarize_batch_failures",
            new=AsyncMock(
                return_value={
                    "failed_records": 2,
                    "successful_records": 165,
                    "total_records": 167,
                    "success_rate": 0.988,
                    "top_errors": [{"message": "INVALID_EMAIL_ADDRESS", "count": 2}],
                    "failed_fields": ["Email"],
                    "error_samples": ["INVALID_EMAIL_ADDRESS"],
                }
            ),
        ), patch(
            "services.migration_records_service.MigrationRecordsService.get_failed_records_for_recovery",
            new=AsyncMock(return_value=[]),
        ), patch(
            "services.planning.recovery_error_classifier.classify_failed_records",
            return_value=type(
                "S",
                (),
                {
                    "recovery_action": "inform_only",
                    "recoverable_count": 0,
                    "unrecoverable_count": 2,
                    "unrecoverable_notice": "Invalid email in source data",
                    "clusters": [
                        {
                            "theme": "Invalid field values",
                            "count": 2,
                            "sample_message": "INVALID_EMAIL_ADDRESS",
                        }
                    ],
                },
            )(),
        ):
            return await build_failure_summary_for_batch("batch-1")

    summary = asyncio.run(_run())
    assert summary is not None
    assert summary["failed_count"] == 2
    assert summary["recovery_action"] == "inform_only"
    assert summary["top_errors"][0]["message"] == "INVALID_EMAIL_ADDRESS"


def test_build_migration_completed_includes_failure_summary():
    from services.migration.completed_event_metadata import build_migration_completed_event_data

    job_id = "mig-job-1"
    job_data = {
        "batch_id": "batch-1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "salesforce_token_id": "token-uuid",
        "successful_records": 165,
        "failed_records": 2,
        "status": "completed",
    }

    fake_summary = {
        "failed_count": 2,
        "top_errors": [{"message": "INVALID_EMAIL_ADDRESS", "count": 2}],
        "recovery_action": "inform_only",
    }

    async def _run():
        with patch(
            "services.migration.failure_summary.build_failure_summary_for_batch",
            new=AsyncMock(return_value=fake_summary),
        ):
            return await build_migration_completed_event_data(job_id, job_data, "user-1")

    event = asyncio.run(_run())
    assert event["failure_summary"] == fake_summary
    assert event["status"] == "partial_success"
    assert event["error_count"] == 2
