"""Tests for inform-only recovery when success rate is high but failures exist."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent.observation import ExecutionObservation, FailureReport, TopError
from agent.recovery_flow import build_inform_only_proposal, maybe_offer_recovery_via_chat
from agent.session import AgentSession
from services.planning.recovery_error_classifier import RecoveryClassificationSummary


def _high_success_observation():
    return ExecutionObservation(
        tool_id="start_migration",
        status="success",
        batch_id="batch-99",
        job_id="job-99",
        metrics={"batch_status": "completed", "success_rate": 0.988},
        failure_report=FailureReport(
            batch_id="batch-99",
            success_rate=0.988,
            failed_count=2,
            successful_records=165,
            total_records=167,
            top_errors=[TopError(message="INVALID_EMAIL_ADDRESS", count=2)],
            failed_fields=["Email"],
        ),
    )


def test_maybe_offer_recovery_inform_only_at_high_success_rate():
    inform_summary = RecoveryClassificationSummary(
        batch_id="batch-99",
        total_failed=2,
        recoverable_count=0,
        unrecoverable_count=2,
        recovery_action="inform_only",
        unrecoverable_notice="Fix invalid email addresses in the source org.",
        clusters=[
            {
                "theme": "Invalid field values",
                "count": 2,
                "sample_message": "INVALID_EMAIL_ADDRESS",
                "resolvable": False,
            }
        ],
    )

    async def _run():
        with patch(
            "agent.recovery_flow.refresh_observation_for_batch",
            new=AsyncMock(return_value=_high_success_observation()),
        ), patch(
            "agent.recovery_flow._load_classification_context",
            new=AsyncMock(return_value=({"destination": "salesforce"}, "migrate contacts")),
        ), patch(
            "agent.recovery_flow.load_recovery_classification",
            new=AsyncMock(return_value=inform_summary),
        ), patch(
            "agent.recovery_flow.build_recovery_proposal",
            new=AsyncMock(side_effect=AssertionError("LLM recovery should not run")),
        ), patch(
            "services.chat_service.ChatService.add_message",
            new=AsyncMock(return_value={"id": "msg-1"}),
        ), patch(
            "services.pipeline_learning.service.PipelineLearningService.enabled",
            return_value=False,
        ):
            return await maybe_offer_recovery_via_chat(
                user_id="user-1",
                conversation_id="conv-1",
                batch_id="batch-99",
                job_id="job-99",
                event_data={"destination": "salesforce"},
            )

    pending = asyncio.run(_run())
    assert pending is not None
    proposal = pending.get("recovery_proposal") or {}
    assert proposal.get("requires_write") is False
    assert proposal.get("inform_only") is True
    assert proposal.get("unrecoverable_count") == 2


def test_build_inform_only_proposal_no_write_steps():
    session = AgentSession(user_id="u1", conversation_id="c1", mode="migrate")
    obs = _high_success_observation()
    classification = RecoveryClassificationSummary(
        batch_id="batch-99",
        total_failed=2,
        recoverable_count=0,
        unrecoverable_count=2,
        recovery_action="inform_only",
        unrecoverable_notice="Invalid emails in source data.",
    )

    async def _run():
        return await build_inform_only_proposal(session, obs, classification)

    proposal = asyncio.run(_run())
    assert proposal["requires_write"] is False
    assert proposal["recoverable_count"] == 0
    assert (proposal.get("recovery_execution_plan") or {}).get("steps") in ([], None)
