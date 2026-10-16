"""Tests for retry_after_partial plan template."""
from __future__ import annotations

from services.planning.plan_builder import build_plan_steps


def test_retry_after_partial_template_skips_extract():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "source_name": "Sheet A",
        "destination_name": "Prod Org",
        "retry_after_partial": True,
        "active_job_id": "job-1",
        "_plan_scope": {"operations": ["map", "migrate"]},
        "recovery_mapping_context": "Fix LastName mapping",
    }
    plan = build_plan_steps(classification, prompt="fix migration errors")
    assert plan.metadata.get("pipeline_label") == "map → migrate"
    tool_ids = [s.parameters.get("tool_id") for s in plan.steps]
    assert tool_ids == ["map_fields", "start_migration"]
    assert plan.steps[0].parameters.get("mapping_context_summary") == "Fix LastName mapping"
