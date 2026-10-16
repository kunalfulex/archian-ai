"""Fresh path compound scope → full plan steps (extract + data_ops + map)."""
from __future__ import annotations

from services.planning.plan_builder import build_plan_steps


def test_fresh_extract_filter_map_plan_steps():
    classification = {
        "source": "salesforce",
        "source_name": "SF 2",
        "source_object_api_name": "Contact",
        "intent": "extract",
        "mode": "extract",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "planner_destination_confirmed": True,
        "_plan_scope": {
            "operations": ["extract", "map"],
            "data_operations": ["filter"],
            "needs_destination": True,
        },
    }
    plan = build_plan_steps(classification, prompt="compound fresh prompt")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_salesforce", "run_data_ops_plan", "map_fields"]
    assert plan.metadata.get("pipeline_steps") == ["extract", "data_ops", "map"]
