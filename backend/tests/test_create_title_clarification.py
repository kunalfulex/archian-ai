"""Create spreadsheet title confirmation and slot gaps."""
from __future__ import annotations

from services.planning.create_metadata import (
    apply_create_metadata_to_classification,
    is_generic_spreadsheet_title,
    spreadsheet_title_slot_satisfied,
)
from services.planning.op_requirements import (
    CREATE_TITLE,
    assess_plan_requirements,
    derive_plan_requirements,
)
from services.planning.plan_scope import sync_scope_into_classification
from tests.plan_scope_helpers import with_plan_scope


def test_generic_spreadsheet_titles():
    assert is_generic_spreadsheet_title("Google Sheet")
    assert is_generic_spreadsheet_title("google sheets")
    assert not is_generic_spreadsheet_title("Contact List")


def test_inferred_generic_title_does_not_satisfy_slot():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Google Sheet",
        "spreadsheet_headers": ["Name", "Email"],
    }
    classification = apply_create_metadata_to_classification(
        {"destination": "google_sheets", "intent": "create", "planner_create_spreadsheet": True},
        goal,
    )
    assert classification.get("planner_suggested_spreadsheet_title") == "Google Sheet"
    assert not classification.get("planner_new_spreadsheet_name")
    assert not spreadsheet_title_slot_satisfied(classification, goal)


def test_create_only_missing_title_gap_when_headers_present():
    classification = with_plan_scope(
        {
            "destination": "google_sheets",
            "intent": "create",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_headers": ["First Name", "Last Name", "Email"],
        },
        operations=["create"],
        create_destination=True,
        spreadsheet_headers=["First Name", "Last Name", "Email"],
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["create"],
    )
    reqs = derive_plan_requirements(["create"], classification=classification)
    gaps, clars = assess_plan_requirements(classification, reqs)
    assert any(g["slot"] == CREATE_TITLE for g in gaps)
    assert any(c.clarification_type == "create_workbook_name" for c in clars)


def test_confirmed_title_satisfies_slot():
    classification = {
        "planner_spreadsheet_title_confirmed": True,
        "planner_new_spreadsheet_name": "Contact Export",
    }
    assert spreadsheet_title_slot_satisfied(classification, {})


def test_confirmed_create_title_satisfies_destination_entity_slot():
    """New workbook title counts as destination target before the sheet exists."""
    from services.planning.op_requirements import DESTINATION_ENTITY

    classification = with_plan_scope(
        {
            "destination": "google_sheets",
            "destination_connector_id": "acc-1",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Lead Data Export",
            "planner_destination_entity_confirmed": True,
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
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
    )
    gaps, _ = assess_plan_requirements(
        classification,
        reqs,
        parsed_goal=classification["_plan_scope"],
    )
    assert not any(g["slot"] == DESTINATION_ENTITY for g in gaps)
