"""Goal-driven fresh path: extract + data_ops from PLAN_SCOPE (no keyword matching)."""
from __future__ import annotations

from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps
from services.planning.plan_builder import build_plan_steps


def test_resolve_fresh_extract_and_filter_prepends_extract():
    goal = {"operations": ["extract"], "data_operations": ["filter"]}
    ctx = PlanContext(active_job_id=None, mapping_ready=False)
    assert resolve_pipeline_steps(goal, ctx) == ["extract", "data_ops"]


def test_resolve_fresh_data_ops_only_trusts_plan_scope():
    """PLAN_SCOPE must include extract when data_ops needs a job; resolver does not inject."""
    goal = {"operations": [], "data_operations": ["filter"]}
    ctx = PlanContext(active_job_id=None, mapping_ready=False)
    assert resolve_pipeline_steps(goal, ctx) == ["data_ops"]


def test_resolve_continuation_data_ops_only_with_job():
    goal = {"operations": [], "data_operations": ["filter"]}
    ctx = PlanContext(active_job_id="job-1", mapping_ready=False, is_continuation=True)
    assert resolve_pipeline_steps(goal, ctx) == ["data_ops"]


def test_fresh_extract_filter_plan_steps():
    """Regression: SF contact extract + invalid email filter must not run data_ops without extract."""
    prompt = (
        "extract first name, last name, email from contact object of sf 1 org, "
        "remove any invalid email id records"
    )
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "source_object_api_name": "Contact",
        "intent": "extract",
        "mode": "extract",
        "data_operations": ["filter"],
        "_plan_scope": {
            "operations": ["extract"],
            "data_operations": ["filter"],
            "entities": [{"label": "Contact", "role": "source"}],
        },
    }
    plan = build_plan_steps(classification, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_salesforce", "run_data_ops_plan"]
    assert plan.metadata.get("pipeline_steps") == ["extract", "data_ops"]
    data_step = plan.steps[1]
    assert data_step.depends_on == ["extract"]
    op_ids = [x["operation_id"] for x in data_step.parameters.get("steps") or []]
    assert op_ids == ["filter"]


def test_fresh_extract_map_migrate_from_plan_scope():
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "source_name": "contact list",
        "destination_name": "contact list",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "data_operations": ["dedupe", "filter"],
        },
    }
    plan = build_plan_steps(classification, prompt="compound migration prompt ignored")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == [
        "extract_google_sheets",
        "run_data_ops_plan",
        "map_fields",
        "start_migration",
    ]
    assert plan.metadata.get("pipeline_label") == "extract → data_ops → map → migrate"
