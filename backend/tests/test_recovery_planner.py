"""Tests for LLM recovery execution plan builder."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.observation import ExecutionObservation, FailureReport, TopError
from agent.session import AgentSession
from services.planning.recovery_planner import (
    _rule_based_recovery_plan,
    plan_recovery_with_llm,
    recovery_plan_to_execution_plan,
)


def test_rule_based_plan_inform_only_when_no_recoverable():
    from services.planning.recovery_error_classifier import RecoveryClassificationSummary

    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.94,
            failed_count=9,
            total_records=165,
            successful_records=156,
            top_errors=[TopError(message="STORAGE_LIMIT_EXCEEDED", count=6)],
        ),
    )
    classification = RecoveryClassificationSummary(
        batch_id="b1",
        total_failed=9,
        recoverable_count=0,
        unrecoverable_count=9,
        recovery_action="inform_only",
    )
    plan = _rule_based_recovery_plan(obs, classification_summary=classification)
    assert len(plan.steps) == 0
    assert plan.ask_user is True


def test_rule_based_plan_at_low_success_still_has_steps():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.25,
            failed_count=75,
            total_records=100,
            successful_records=25,
            failed_fields=["LastName", "AccountId"],
            top_errors=[TopError(message="Required fields are missing: LastName", count=40)],
        ),
    )
    plan = _rule_based_recovery_plan(obs)
    assert len(plan.steps) == 2
    assert plan.steps[0].tool_id == "map_fields"
    assert plan.steps[1].tool_id == "start_migration"
    assert plan.ask_user is True
    assert plan.error_clusters


def test_recovery_execution_plan_shape():
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="partial",
        failure_report=FailureReport(
            success_rate=0.72,
            failed_count=28,
            total_records=100,
            successful_records=72,
            failed_fields=["Email"],
        ),
    )
    plan = _rule_based_recovery_plan(obs)
    raw = recovery_plan_to_execution_plan(plan)
    assert raw["metadata"]["template"] == "recovery"
    assert len(raw["steps"]) >= 2


def test_llm_recovery_plan_parses_steps():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        classification_snapshot={
            "plan_contract": {
                "migration_intent": {
                    "include_related_data": True,
                    "parent_entity_key": "Account",
                    "driving_entity_key": "Contact",
                }
            }
        },
    )
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        failure_report=FailureReport(
            success_rate=0.1,
            failed_count=90,
            total_records=100,
            successful_records=10,
            failed_fields=["AccountId"],
            top_errors=[TopError(message="Foreign key external id not found for field AccountId", count=50)],
        ),
    )

    llm_json = """
    {
      "what_went_wrong": "Most records failed because Account lookups could not be resolved.",
      "error_clusters": [{"theme": "Lookup resolution", "count": 50, "sample_message": "Foreign key...", "fields": ["AccountId"]}],
      "relationship_notice": "Contact rows reference Account — verify AccountName was mapped for lookup resolve.",
      "mapping_context_summary": "Map AccountName for lookup resolution on Contact.AccountId",
      "ask_user": true,
      "rationale": "Fix lookup mapping then retry",
      "confidence": "low",
      "steps": [
        {"step_type": "map", "label": "Re-map lookup fields", "tool_id": "map_fields"},
        {"step_type": "migrate", "label": "Retry migration", "tool_id": "start_migration"}
      ]
    }
    """

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new=AsyncMock(
                return_value=type(
                    "R",
                    (),
                    {"choices": [type("C", (), {"message": type("M", (), {"content": llm_json})()})()]},
                )()
            ),
        ):
            return await plan_recovery_with_llm(session, obs)

    plan = asyncio.run(_run())
    assert "Account" in (plan.relationship_notice or "")
    assert len(plan.steps) == 2
    assert plan.mapping_context_summary


def test_build_recovery_proposal_includes_execution_plan():
    from agent.recovery_flow import build_recovery_proposal

    session = AgentSession(user_id="u1", conversation_id="c1", mode="migrate", active_job_id="job-1")
    obs = ExecutionObservation(
        tool_id="start_migration",
        status="failed",
        batch_id="batch-1",
        job_id="job-1",
        failure_report=FailureReport(
            success_rate=0.0,
            failed_count=10,
            total_records=10,
            successful_records=0,
            failed_fields=["Name"],
        ),
    )

    async def _run():
        from services.planning.recovery_error_classifier import RecoveryClassificationSummary

        classification = RecoveryClassificationSummary(
            batch_id="batch-1",
            total_failed=10,
            recoverable_count=10,
            unrecoverable_count=0,
            recoverable_record_indices=list(range(10)),
            recovery_action="remap_and_retry",
        )
        with patch(
            "agent.recovery_flow._diagnose_with_tools",
            new=AsyncMock(return_value=({"narrative": "All failed"}, None)),
        ), patch(
            "agent.recovery_flow.load_recovery_classification",
            new=AsyncMock(return_value=classification),
        ), patch(
            "agent.registry.default_registry",
        ) as mock_reg:
            mock_reg.return_value.invoke = AsyncMock(
                return_value=type(
                    "TR",
                    (),
                    {"ok": True, "user_visible": {"proposal": {"mapping_context_summary": "Fix Name"}}},
                )()
            )
            return await build_recovery_proposal(session, obs)

    proposal = asyncio.run(_run())
    assert proposal.get("what_went_wrong")
    assert proposal.get("recovery_execution_plan", {}).get("steps")
    assert proposal.get("requires_write") is True
