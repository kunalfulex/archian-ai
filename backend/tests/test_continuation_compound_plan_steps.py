"""Continuation compound scope → data_ops + map steps."""
from __future__ import annotations

from services.planning.continuation_policy import (
    apply_continuation_goal_to_classification,
    resolve_continuation_steps,
)
from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps
from services.planning.plan_builder import build_plan_steps


class _JobStub:
    job_id = "job-1"
    mapping_ready = False
    pipeline_stage = "extracted"
    last_completed_step = "extract"


def test_continuation_compound_goal_to_steps():
    goal = {"operations": ["map"], "data_operations": ["filter"], "needs_destination": True}
    steps = resolve_continuation_steps(goal, _JobStub(), continuation_phase="data_ops")
    assert steps == ["data_ops", "map"]


def test_continuation_compound_plan_builder():
    cls = {
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "source": "salesforce",
        "source_name": "SF 2",
        "source_object_api_name": "Contact",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_object_api_name": "Account",
        "planner_destination_confirmed": True,
        "_continuation_steps": ["data_ops", "map"],
        "_plan_scope": {
            "operations": ["map"],
            "data_operations": ["filter"],
        },
    }
    plan = build_plan_steps(cls, prompt="filter and map")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["run_data_ops_plan", "map_fields"]
