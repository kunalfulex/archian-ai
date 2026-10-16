"""Approved planner plans and flow connector resolution."""

import pytest
from unittest.mock import MagicMock

from agent.planner import Planner
from agent.session import AgentSession


def test_approved_data_ops_plan_uses_v2_steps_not_dynamic_build():
    """Approved data_ops execution_plan must run once — not rebuild via _build_data_ops_plan."""
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="data_ops",
        active_job_id="job-123",
        classification_snapshot={
            "plan_status": "approved",
            "execution_plan": {
                "status": "approved",
                "steps": [
                    {
                        "step_type": "transform",
                        "label": "Remove duplicates",
                        "parameters": {
                            "tool_id": "run_data_ops_plan",
                            "steps": [{"operation_id": "dedupe", "prompt": "dedupe by email"}],
                        },
                    }
                ],
            },
        },
    )
    planner = Planner()

    import asyncio

    plan = asyncio.run(planner.build_plan("dedupe by email", session))
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_id == "run_data_ops_plan"
    assert plan.reasoning.startswith("plan_scoped:")
