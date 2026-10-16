"""Execution-oriented goal_summary for execution plan UI."""
from __future__ import annotations

from services.planning.plan_builder import apply_plan_execution_summary, build_plan_steps
from services.planning.schemas import ExecutionPlan, PlanStep, StepType


def test_map_migrate_continuation_summary_uses_job_prefix():
    plan = ExecutionPlan(
        status="draft",
        metadata={
            "continuation": True,
            "job_id": "job-123",
            "pipeline_steps": ["map", "migrate"],
        },
        steps=[
            PlanStep(
                step_type=StepType.MAP,
                label="Map fields to Contact in SF 1",
                parameters={"tool_id": "map_fields"},
            ),
            PlanStep(
                step_type=StepType.MIGRATE,
                label="Migrate to SF 1",
                parameters={"tool_id": "start_migration"},
            ),
        ],
    )
    out = apply_plan_execution_summary(plan)
    assert out.goal_summary
    assert out.goal_summary.startswith("Using data from your current job,")
    assert "Map fields to Contact in SF 1" in out.goal_summary
    assert "Migrate to SF 1" in out.goal_summary
    assert "user is" not in out.goal_summary.lower()


def test_extract_map_migrate_summary_from_step_labels():
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 2",
        "destination_name": "SF 1",
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    plan = build_plan_steps(classification, prompt="extract contacts and migrate to SF 1")
    assert plan.goal_summary
    assert "Extract from" in plan.goal_summary
    assert "Map fields" in plan.goal_summary
    assert "Migrate to SF 1" in plan.goal_summary


def test_create_only_summary():
    plan = ExecutionPlan(
        status="draft",
        metadata={
            "pipeline_steps": ["create"],
            "classification": {
                "planner_new_spreadsheet_name": "Leads Export",
                "planner_spreadsheet_headers": ["Name", "Email"],
            },
            "goal": {},
        },
        steps=[
            PlanStep(
                step_type=StepType.CREATE,
                label='Create spreadsheet "Leads Export"',
                parameters={"tool_id": "google_sheets_create_from_prompt"},
            ),
        ],
    )
    out = apply_plan_execution_summary(plan)
    assert out.goal_summary
    assert out.goal_summary.startswith("Create a new Google Sheet")
    assert "Leads Export" in out.goal_summary
    assert "Name" in out.goal_summary


def test_empty_steps_clears_summary():
    plan = ExecutionPlan(status="draft", goal_summary="stale", steps=[])
    out = apply_plan_execution_summary(plan)
    assert out.goal_summary is None
