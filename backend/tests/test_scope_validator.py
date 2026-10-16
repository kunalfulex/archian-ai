"""PLAN_SCOPE validation — strip incoherent ops, never append (except map before migrate)."""
from __future__ import annotations

from services.planning.create_metadata import sync_export_pipeline_operations
from services.planning.goal_parser import parse_goal
from services.planning.plan_builder import build_plan_steps
from services.planning.scope_validator import (
    validate_and_normalize_plan_scope,
    validate_plan_scope_goal,
    validate_plan_scope_ops,
)


def test_validate_scope_preserves_create_ops():
    scope = {
        "pipeline_ops": ["create"],
        "operations": ["create"],
        "create_destination": True,
        "needs_destination": True,
    }
    cls = {"intent": "extract", "mode": "extract", "source": "salesforce", "source_name": "SF 1"}
    out_scope, out_cls = validate_and_normalize_plan_scope(scope, cls, "no just create google sheet")
    assert out_scope["operations"] == ["create"]
    assert out_scope.get("create_destination") is True
    assert out_cls["intent"] == "extract"


def test_validate_scope_preserves_extract_when_source_resolved():
    scope = {
        "pipeline_ops": ["extract"],
        "operations": ["extract"],
        "needs_destination": False,
    }
    cls = {"source": "salesforce", "source_name": "SF 2", "source_connector_id": "sf-1"}
    out_scope, _ = validate_and_normalize_plan_scope(scope, cls, "extract company details from sf 2 org")
    assert out_scope["operations"] == ["extract"]


def test_validate_strips_extract_map_migrate_without_source():
    """Mis-scoped export pipeline → create-only when source is unknown."""
    ops = validate_plan_scope_ops(
        ["extract", "create", "map", "migrate"],
        classification={
            "intent": "create",
            "source": "unknown",
            "destination": "google_sheets",
            "destination_name": "Friends list",
        },
    )
    assert ops == ["create"]


def test_validate_preserves_full_export_pipeline_with_resolved_source():
    ops = validate_plan_scope_ops(
        ["extract", "create", "map", "migrate"],
        classification={
            "source": "salesforce",
            "source_name": "SF 1",
            "source_connector_id": "sf-1",
            "destination": "google_sheets",
            "destination_name": "Export",
        },
    )
    assert ops == ["extract", "create", "map", "migrate"]


def test_sync_export_does_not_append_map_migrate():
    goal = {"operations": ["extract", "create"]}
    cls = {
        "source": "unknown",
        "destination": "google_sheets",
        "destination_name": "Sheet",
        "planner_destination_confirmed": True,
    }
    out = sync_export_pipeline_operations(goal, cls)
    assert out["operations"] == ["create"]


def test_parse_goal_create_intent_defaults_to_create():
    goal = parse_goal({"intent": "create", "mode": "create"})
    assert goal["operations"] == ["create"]


def test_create_only_plan_after_scope_validation():
    classification = {
        "destination": "google_sheets",
        "intent": "create",
        "mode": "create",
        "source": "unknown",
        "planner_create_spreadsheet": True,
        "planner_spreadsheet_title_confirmed": True,
        "planner_new_spreadsheet_name": "Friends list",
        "planner_spreadsheet_headers": ["First name", "Last name", "Email"],
    }
    scope, _ = validate_and_normalize_plan_scope(
        {
            "operations": ["extract", "create", "map", "migrate"],
            "create_destination": True,
            "spreadsheet_title": "Friends list",
            "spreadsheet_headers": ["First name", "Last name", "Email"],
        },
        classification,
    )
    classification["_plan_scope"] = scope
    classification["operations"] = scope["operations"]
    plan = build_plan_steps(classification, prompt="Friends list")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["google_sheets_create_from_prompt"]
    assert plan.metadata.get("pipeline_steps") == ["create"]


def test_validate_preserves_extract_when_needs_source_before_bind():
    """Fresh extract: keep extract until source connector is bound."""
    ops = validate_plan_scope_ops(
        ["extract"],
        classification={"source": None, "intent": "unknown"},
        parsed_goal={
            "needs_source": True,
            "source_phrase": "SF 1",
            "operations": ["extract"],
        },
    )
    assert ops == ["extract"]


def test_validate_strips_extract_without_needs_source_or_phrase():
    ops = validate_plan_scope_ops(
        ["extract"],
        classification={"source": None, "intent": "unknown"},
    )
    assert ops == []


def test_validate_strips_map_only_on_active_job_without_destination():
    scope = {
        "operations": ["map"],
        "data_operations": ["filter"],
        "needs_destination": False,
    }
    cls = {"active_job_id": "job-1", "source": "salesforce", "source_name": "SF 1"}
    out = validate_plan_scope_goal(scope, cls)
    assert out.get("operations") in (None, [])


def test_validate_ensure_map_before_migrate_on_active_job():
    scope = {"operations": ["migrate"], "needs_destination": True}
    cls = {"active_job_id": "job-1", "source": "salesforce", "source_name": "SF 1"}
    out = validate_plan_scope_goal(scope, cls)
    assert out["operations"] == ["map", "migrate"]
