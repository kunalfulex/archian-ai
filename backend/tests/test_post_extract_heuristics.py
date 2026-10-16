"""Heuristic post-extract rule proposals."""

from post_extract.rules_proposer import heuristic_rules_for_prompt
from services.planning.plan_builder import build_plan_steps, finalize_plan_status


def test_heuristic_invalid_email_filter():
    rules = heuristic_rules_for_prompt(
        "remove invalid email id records from the extracted data",
        ["Email", "FirstName", "LastName"],
    )
    assert rules
    conds = rules["filter_rules"]["conditions"]
    assert conds[0]["field"] == "Email"
    assert conds[0]["operator"] == "valid_email"


def test_plan_template_data_ops_when_active_job_and_filter_prompt():
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "active_job_id": "job-abc",
        "mode": "data_ops",
        "operations": ["filter"],
        "intent": "filter",
        "_continuation_steps": ["data_ops"],
        "_plan_scope": {"data_operations": ["filter"]},
    }
    plan = build_plan_steps(
        classification,
        prompt="remove invalid email id records from extracted data",
    )
    plan = finalize_plan_status(plan)
    assert plan.metadata.get("pipeline_label") == "data_ops"
    assert len(plan.steps) == 1
    assert plan.steps[0].parameters.get("tool_id") == "run_data_ops_plan"
    assert plan.steps[0].parameters.get("parent_job_id") == "job-abc"
    op_ids = [x["operation_id"] for x in plan.steps[0].parameters.get("steps") or []]
    assert op_ids == ["filter"]
