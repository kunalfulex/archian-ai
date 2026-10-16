"""Create header provenance — pick_create_headers only for create-only flows."""
from __future__ import annotations

from services.planning.create_contract import build_create_spreadsheet_contract
from services.planning.create_metadata import (
    apply_create_metadata_to_classification,
    create_headers_from_extraction,
    filter_create_header_clarifications,
    should_require_create_headers_clarification,
)
from services.planning.goal_parser import _apply_plan_scope_llm_data
from services.planning.op_requirements import (
    CREATE_HEADERS,
    assess_plan_requirements,
    append_requirement_clarifications_to_plan,
    derive_plan_requirements,
)
from services.planning.schemas import Clarification, ExecutionPlan
from services.planning.plan_scope import sync_scope_into_classification
from tests.plan_scope_helpers import with_plan_scope


def test_create_headers_from_extraction_when_extract_in_pipeline():
    cls = {"_pipeline_tokens": ["extract", "create", "map", "migrate"]}
    assert create_headers_from_extraction(cls) is True
    assert should_require_create_headers_clarification(cls) is False


def test_create_only_requires_header_clarification_when_empty():
    goal = {"operations": ["create"], "create_destination": True}
    cls = apply_create_metadata_to_classification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        goal,
    )
    assert create_headers_from_extraction(cls, goal) is False
    assert should_require_create_headers_clarification(cls, goal) is True


def test_extract_create_plan_has_no_create_headers_slot():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Lead Data Export",
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
        spreadsheet_title="Lead Data Export",
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "create", "map", "migrate"],
    )
    reqs = derive_plan_requirements(
        ["extract", "create", "map", "migrate"],
        classification=classification,
        parsed_goal=classification["_plan_scope"],
    )
    slots = {s.slot for s in reqs.all_slots()}
    assert CREATE_HEADERS not in slots

    gaps, clars = assess_plan_requirements(
        classification,
        reqs,
        parsed_goal=classification["_plan_scope"],
    )
    assert not any(g["slot"] == CREATE_HEADERS for g in gaps)
    assert not any(c.clarification_type == "pick_create_headers" for c in clars)


def test_extract_create_append_plan_never_pick_create_headers():
    goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
        "spreadsheet_title": "Lead Data Export",
    }
    classification = apply_create_metadata_to_classification(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "destination_connector_id": "acc-1",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Lead Data Export",
            "extract_fields_confirmed": True,
        },
        goal,
    )
    classification["_pipeline_tokens"] = ["extract", "create", "map", "migrate"]
    reqs = derive_plan_requirements(
        ["extract", "create", "map", "migrate"],
        classification=classification,
        parsed_goal=goal,
    )
    plan = append_requirement_clarifications_to_plan(
        ExecutionPlan(),
        classification,
        reqs,
        parsed_goal=goal,
    )
    assert not any(c.clarification_type == "pick_create_headers" for c in plan.clarifications)


def test_plan_scope_strips_headers_on_extract_create():
    base = {"operations": ["extract", "create", "map", "migrate"], "create_destination": True}
    out = _apply_plan_scope_llm_data(
        base,
        {
            "pipeline_ops": ["extract", "create", "map", "migrate"],
            "create_destination": True,
            "spreadsheet_headers": ["First Name", "Last Name", "Email"],
        },
    )
    assert "spreadsheet_headers" not in out


def test_apply_create_metadata_skips_llm_headers_when_extract_backed():
    goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
        "spreadsheet_headers": ["First Name", "Last Name"],
    }
    out = apply_create_metadata_to_classification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        goal,
    )
    assert "planner_spreadsheet_headers" not in out


def test_create_contract_use_extraction_headers_for_export_flow():
    goal = {"operations": ["extract", "create", "map", "migrate"], "create_destination": True}
    cls = {
        "planner_create_spreadsheet": True,
        "planner_new_spreadsheet_name": "Lead Data Export",
        "_pipeline_tokens": ["extract", "create", "map", "migrate"],
    }
    contract = build_create_spreadsheet_contract(cls, goal)
    assert contract["use_extraction_headers"] is True
    assert contract["headers"] == []


def test_filter_create_header_clarifications():
    clars = [
        Clarification(
            id="1",
            clarification_type="pick_create_headers",
            prompt="Pick headers",
            options=[],
        ),
        Clarification(
            id="2",
            clarification_type="create_workbook_name",
            prompt="Name sheet",
            options=[],
        ),
    ]
    cls = {"_pipeline_tokens": ["extract", "create", "map", "migrate"]}
    filtered = filter_create_header_clarifications(clars, cls)
    assert len(filtered) == 1
    assert filtered[0].clarification_type == "create_workbook_name"
