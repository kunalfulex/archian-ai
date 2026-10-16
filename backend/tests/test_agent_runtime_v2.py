"""Tests for Agent Runtime v2 observation, recovery policy, and orchestrator."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.observation import ExecutionObservation, FailureReport, TopError
from agent.recovery_orchestrator import RecoveryOrchestrator
from agent.recovery_policy import (
    MIN_SUCCESS_RATE_OK,
    migration_chunk_type,
    migration_event_type,
    should_recover,
)
from agent.session import AgentSession
from services.migration_records_service import MigrationRecordsService


def test_extract_fields_from_bracket_list():
    msg = "Required fields are missing: [LastName, Company]"
    fields = MigrationRecordsService._extract_fields_from_error(msg)
    assert "LastName" in fields
    assert "Company" in fields


def test_migration_chunk_type_tiers():
    assert migration_chunk_type(0.99) == "migration_outcome"
    assert migration_chunk_type(MIN_SUCCESS_RATE_OK) == "migration_outcome"
    assert migration_chunk_type(0.80) == "migration_partial"
    assert migration_chunk_type(0.30) == "migration_failed"


def test_migration_event_type_tiers():
    assert migration_event_type(0.99) == "migration_success"
    assert migration_event_type(0.80) == "migration_partial"
    assert migration_event_type(0.30) == "migration_failed"


def test_should_recover_partial():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        failure_report=FailureReport(
            success_rate=0.7,
            failed_count=30,
            successful_records=70,
            total_records=100,
        ),
    )
    assert should_recover(obs) is True


def test_should_not_recover_ok():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="success",
        failure_report=FailureReport(
            success_rate=0.98,
            failed_count=2,
            successful_records=98,
            total_records=100,
        ),
    )
    assert should_recover(obs) is False


def test_should_not_recover_while_batch_in_progress():
    from agent.recovery_policy import migration_batch_in_progress, migration_batch_status_is_terminal

    assert migration_batch_status_is_terminal("completed") is True
    assert migration_batch_status_is_terminal("in_progress") is False
    assert migration_batch_in_progress(
        batch_status="in_progress",
        total_records=10,
        successful_records=10,
        failed_records=0,
    ) is True
    assert migration_batch_in_progress(total_records=10, successful_records=0, failed_records=0) is True
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="submitted",
        batch_id="batch-1",
    )
    assert should_recover(obs) is False
    obs_partial = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        metrics={"batch_status": "in_progress"},
        failure_report=FailureReport(
            success_rate=0.0,
            failed_count=0,
            successful_records=0,
            total_records=10,
        ),
    )
    assert should_recover(obs_partial) is False


def test_should_recover_after_batch_finalized_with_failures():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        metrics={"batch_status": "completed"},
        failure_report=FailureReport(
            success_rate=0.0,
            failed_count=10,
            successful_records=0,
            total_records=10,
        ),
    )
    assert should_recover(obs) is True


def test_recovery_orchestrator_partial_with_fields():
    orchestrator = RecoveryOrchestrator()
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        failure_report=FailureReport(
            success_rate=0.72,
            failed_count=28,
            total_records=100,
            successful_records=72,
            failed_fields=["LastName", "Email"],
            top_errors=[TopError(message="Required field missing: LastName", count=20)],
        ),
    )
    decision = orchestrator.decide(
        obs,
        analyze_visible={"narrative": "28 records failed on LastName."},
    )
    assert decision.tool_id == "map_fields"
    assert decision.ask_user is False
    assert "LastName" in decision.arguments.get("mapping_context_summary", "")
    assert orchestrator.requires_write_approval(decision) is True
    plan = orchestrator.build_execution_plan(decision)
    assert len(plan["steps"]) == 2
    assert plan["steps"][0]["step_type"] == "map"
    assert plan["steps"][1]["step_type"] == "migrate"


def test_recovery_orchestrator_catastrophic_with_fields_still_proposes_remap():
    orchestrator = RecoveryOrchestrator()
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.25,
            failed_count=75,
            total_records=100,
            successful_records=25,
            failed_fields=["LastName"],
        ),
    )
    decision = orchestrator.decide(obs)
    assert decision.tool_id == "map_fields"
    assert decision.ask_user is True
    plan = orchestrator.build_execution_plan(decision)
    assert len(plan["steps"]) == 2


def test_recovery_orchestrator_catastrophic_asks_user_without_field_hints():
    orchestrator = RecoveryOrchestrator()
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.25,
            failed_count=75,
            total_records=100,
            successful_records=25,
        ),
    )
    decision = orchestrator.decide(obs)
    assert decision.ask_user is True
    assert decision.tool_id is None
    assert orchestrator.requires_write_approval(decision) is False


def test_apply_recovery_approval_builds_execution_plan():
    from services.planning.plan_execution import apply_recovery_approval

    pending = {
        "classification": {"plan_status": "approved", "active_job_id": "job-1", "execution_plan": {"steps": [{"step_type": "extract"}]}},
        "recovery_proposal": {
            "requires_write": True,
            "recovery_classification": {
                "recoverable_count": 2,
                "recoverable_record_indices": [1, 2],
            },
            "decision": {
                "tool_id": "map_fields",
                "arguments": {"mapping_context_summary": "Fix LastName mapping"},
            },
            "recovery_execution_plan": {
                "steps": [
                    {"step_type": "map", "parameters": {"tool_id": "map_fields"}},
                    {"step_type": "migrate", "parameters": {"tool_id": "start_migration"}},
                ],
            },
        },
        "recovery_batch_id": "batch-1",
        "recovery_job_id": "job-1",
    }
    out = apply_recovery_approval(pending, skip_recovery=False)
    assert out["recovery_approved"] is True
    assert out["recovery_iteration_count"] == 1
    assert out["execution_plan"]["steps"]
    assert out["recovery_mapping_context"] == "Fix LastName mapping"
    assert out["recoverable_record_indices"] == [1, 2]


def test_apply_recovery_dismiss_clears_execution_plan():
    from services.planning.plan_execution import apply_recovery_approval

    pending = {
        "classification": {
            "plan_status": "approved",
            "execution_plan": {"steps": [{"step_type": "extract"}]},
        },
        "recovery_proposal": {"requires_write": True},
        "recovery_batch_id": "batch-1",
    }
    out = apply_recovery_approval(pending, skip_recovery=True)
    assert out["recovery_dismissed"] is True
    assert out["pipeline_complete"] is True
    assert "execution_plan" not in out


def test_recovery_decide_with_llm_uses_llm_pick():
    orchestrator = RecoveryOrchestrator()
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        active_job_id="job-1",
        mapping_id="map-1",
    )
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        batch_id="batch-1",
        failure_report=FailureReport(
            success_rate=0.72,
            failed_count=28,
            total_records=100,
            successful_records=72,
            failed_fields=["LastName"],
        ),
    )

    async def _run():
        with patch.object(
            orchestrator,
            "_llm_pick",
            new=AsyncMock(
                return_value=orchestrator.decide(
                    obs,
                    analyze_visible={"narrative": "Fix LastName"},
                )
            ),
        ), patch.object(orchestrator, "_log_recovery_pick", new=AsyncMock()):
            decision = await orchestrator.decide_with_llm(
                session,
                obs,
                analyze_visible={"narrative": "Fix LastName"},
            )
            assert decision.tool_id == "map_fields"
            orchestrator._log_recovery_pick.assert_awaited_once()
            assert orchestrator._log_recovery_pick.await_args.kwargs["source"] == "llm"

    asyncio.run(_run())


def test_recovery_decide_with_llm_falls_back_to_rules():
    orchestrator = RecoveryOrchestrator()
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="migrate",
        active_job_id="job-1",
    )
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.25,
            failed_count=75,
            total_records=100,
            successful_records=25,
        ),
    )

    async def _run():
        with patch.object(orchestrator, "_llm_pick", new=AsyncMock(return_value=None)), patch.object(
            orchestrator, "_log_recovery_pick", new=AsyncMock()
        ):
            decision = await orchestrator.decide_with_llm(session, obs)
            assert decision.ask_user is True
            assert orchestrator._log_recovery_pick.await_args.kwargs["source"] == "rules"

    asyncio.run(_run())
