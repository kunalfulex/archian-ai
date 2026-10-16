"""PLAN_SCOPE create metadata merge and classification projection."""
from __future__ import annotations

from services.planning.create_metadata import (
    apply_create_metadata_to_classification,
    goal_explicitly_creates_spreadsheet,
)
from services.planning.goal_parser import _apply_plan_scope_llm_data, parse_goal
from services.planning.plan_builder import build_plan_steps


def test_apply_plan_scope_llm_data_create_only():
    base = parse_goal({"intent": "general", "mode": "general"})
    merged = _apply_plan_scope_llm_data(
        base,
        {
            "pipeline_ops": ["create"],
            "create_destination": True,
            "spreadsheet_title": "Sales Contacts",
            "spreadsheet_headers": ["Name", "Email"],
            "needs_destination": True,
            "data_ops_requested": False,
            "confidence": 0.95,
        },
    )
    assert merged["operations"] == ["create"]
    assert merged["create_destination"] is True
    assert merged["spreadsheet_title"] == "Sales Contacts"
    assert merged["spreadsheet_headers"] == ["Name", "Email"]


def test_apply_create_metadata_to_classification():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "CRM",
        "spreadsheet_headers": ["Name"],
    }
    out = apply_create_metadata_to_classification({"destination": "unknown"}, goal)
    assert out["planner_create_spreadsheet"] is True
    assert out["planner_suggested_spreadsheet_title"] == "CRM"
    assert "planner_new_spreadsheet_name" not in out
    assert out["destination"] == "google_sheets"
    assert out["planner_spreadsheet_headers"] == ["Name"]

    confirmed = apply_create_metadata_to_classification(
        {"destination": "unknown", "planner_spreadsheet_title_confirmed": True},
        goal,
    )
    assert confirmed["planner_new_spreadsheet_name"] == "CRM"


def test_goal_explicitly_creates_spreadsheet():
    assert goal_explicitly_creates_spreadsheet({"operations": ["create"]}) is True
    assert goal_explicitly_creates_spreadsheet({"operations": ["extract"]}) is False


def test_standalone_create_plan_from_llm_plan_scope():
    classification = {
        "destination": "google_sheets",
        "intent": "create",
        "operations": ["create"],
    }
    classification = apply_create_metadata_to_classification(
        classification,
        {
            "operations": ["create"],
            "create_destination": True,
            "spreadsheet_title": "Sales Contacts",
            "spreadsheet_headers": ["Name", "Email"],
        },
    )
    classification["planner_spreadsheet_title_confirmed"] = True
    classification["planner_new_spreadsheet_name"] = "Sales Contacts"
    classification["_plan_scope"] = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Sales Contacts",
        "spreadsheet_headers": ["Name", "Email"],
    }
    plan = build_plan_steps(classification, prompt="ignored")
    assert len(plan.steps) == 1
    assert plan.steps[0].parameters.get("tool_id") == "google_sheets_create_from_prompt"
    assert plan.steps[0].parameters.get("headers") == ["Name", "Email"]
