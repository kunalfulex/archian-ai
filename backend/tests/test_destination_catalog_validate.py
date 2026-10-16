"""Tests for LLM-first destination catalog validation (no keyword routing)."""
from __future__ import annotations

from services.planning.destination_catalog_validate import (
    clear_destination_when_not_needed,
    destination_connector_slot_satisfied,
    mark_destination_validated_from_llm,
    needs_google_sheets_destination_clarification,
    read_needs_destination,
    strip_unvalidated_destination_confirm_flags,
    validate_destination_connector_pins,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        salesforce_connections=[SalesforceConnectionRef(name="SF 1", token_id="tok-1")],
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-1", name="Work Account"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                spreadsheet_id="sid-1",
            ),
        ],
        has_google_sheets=True,
    )


def test_extract_only_does_not_need_gsheets_destination_clarification():
    cls = {
        "source": "salesforce",
        "destination": "google_sheets",
        "_plan_scope": {"operations": ["extract"], "needs_destination": False},
    }
    goal = {"operations": ["extract"], "needs_destination": False}
    assert read_needs_destination(cls, goal) is False
    assert (
        needs_google_sheets_destination_clarification(cls, _catalog(), parsed_goal=goal)
        is False
    )


def test_extract_only_clears_destination_pins():
    cls = {
        "destination": "google_sheets",
        "destination_name": "contact list",
        "planner_destination_confirmed": True,
    }
    goal = {"operations": ["extract"], "needs_destination": False}
    out = clear_destination_when_not_needed(cls, goal)
    assert out.get("destination") is None
    assert not read_needs_destination(out, goal)


def test_unvalidated_confirm_stripped():
    cls = {
        "destination": "google_sheets",
        "destination_name": "contact list",
        "planner_destination_confirmed": True,
    }
    out = strip_unvalidated_destination_confirm_flags(cls)
    assert out.get("planner_destination_confirmed") is None


def test_validated_llm_pick_satisfies_slot():
    cat = _catalog()
    cls = mark_destination_validated_from_llm(
        {
            "destination": "google_sheets",
            "destination_name": "contact list",
            "destination_spreadsheet_id": "sid-1",
            "google_sheets_connection_id": "conn-1",
        },
        catalog=cat,
    )
    goal = {"operations": ["extract", "map", "migrate"], "needs_destination": True}
    assert validate_destination_connector_pins(cls, cat)
    assert destination_connector_slot_satisfied(cls, cat, parsed_goal=goal)


def test_single_account_requires_connection_id_for_slot():
    from services.planning.destination_catalog_validate import google_sheets_account_slot_satisfied

    cat = _catalog()
    cls = {"destination": "google_sheets", "destination_name": "contact list"}
    assert google_sheets_account_slot_satisfied(cls, cat) is False


def test_mark_validated_pins_sole_google_account():
    cat = _catalog()
    cls = mark_destination_validated_from_llm(
        {
            "destination": "google_sheets",
            "destination_name": "contact list",
            "destination_spreadsheet_id": "sid-1",
        },
        catalog=cat,
    )
    assert cls.get("google_sheets_connection_id") == "conn-1"
    assert cls.get("destination_connector_id") == "conn-1"


def test_classifier_dest_without_validation_not_satisfied():
    cat = _catalog()
    cls = {
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_spreadsheet_id": "sid-1",
        "planner_destination_confirmed": True,
    }
    goal = {"operations": ["extract", "map", "migrate"], "needs_destination": True}
    out = strip_unvalidated_destination_confirm_flags(cls)
    assert not destination_connector_slot_satisfied(out, cat, parsed_goal=goal)
