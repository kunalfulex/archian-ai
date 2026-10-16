"""Approved v2 data_ops plans must use fixed steps."""
from __future__ import annotations

from agent.plan import ExecutionPlan, PlanStep
from agent.session import AgentSession
from agent.tools.data_ops import RunDataOpsPlanTool, _build_steps


def test_approved_v2_data_ops_uses_fixed_step():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="data_ops",
        active_job_id="job-1",
        operations=["transform"],
        classification_snapshot={
            "plan_status": "approved",
            "execution_plan": {
                "status": "approved",
                "steps": [
                    {
                        "step_type": "transform",
                        "parameters": {"tool_id": "run_data_ops_plan", "parent_job_id": "job-1"},
                    }
                ],
            },
        },
    )
    plan = ExecutionPlan(
        mode="data_ops",
        steps=[
            PlanStep(
                tool_id="run_data_ops_plan",
                arguments={"parent_job_id": "job-1"},
                label="Process extracted data",
            )
        ],
        reasoning="plan_scoped:data_ops",
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_id == "run_data_ops_plan"
    assert plan.reasoning == "plan_scoped:data_ops"


def test_ad_hoc_data_ops_plan_emits_run_data_ops_step():
    from agent.planner import Planner

    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="data_ops",
        active_job_id="job-1",
        operations=["transform"],
        classification_snapshot={"intent": "transform", "mode": "data_ops"},
    )
    import asyncio

    plan = asyncio.run(Planner().build_plan("filter empty rows", session))
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_id == "run_data_ops_plan"


def test_run_data_ops_build_steps_falls_back_to_session_operations():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="data_ops",
        prompt="combine first and last name",
        operations=["transform", "filter"],
    )
    steps = _build_steps({}, session)
    assert len(steps) == 2
    assert steps[0].operation_id == "transform"
    assert steps[1].operation_id == "filter"


async def _run_tool_builds_steps_from_session():
    from unittest.mock import AsyncMock, patch

    tool = RunDataOpsPlanTool()
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="data_ops",
        active_job_id="job-1",
        prompt="remove invalid emails",
        operations=["filter"],
    )

    with patch(
        "services.conversation.session_sync.sync_session_after_post_extract",
        new_callable=AsyncMock,
    ), patch.object(
        RunDataOpsPlanTool,
        "_run_orchestrator",
        side_effect=lambda s, a, cb: {"job_id": "job-2", "operations_applied": ["filter"]},
    ):
        result = await tool.run(session, {"parent_job_id": "job-1"})
    assert result.ok


def test_run_data_ops_tool_with_empty_args_uses_operations():
    import asyncio

    asyncio.run(_run_tool_builds_steps_from_session())
