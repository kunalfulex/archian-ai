"""Tests for LLM-first source catalog validation."""
from __future__ import annotations

from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)
from services.planning.source_catalog_validate import (
    clear_source_when_not_needed,
    mark_source_validated_from_llm,
    read_needs_source,
    source_connector_slot_satisfied,
    strip_unvalidated_source_confirm_flags,
    validate_source_connector_pins,
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


def test_file_upload_does_not_need_source():
    cls = {
        "source": "file",
        "source_name": "data.csv",
        "_plan_scope": {"operations": ["extract"], "needs_source": True},
    }
    assert read_needs_source(cls) is False


def test_extract_needs_source_from_plan_scope():
    cls = {
        "_plan_scope": {"operations": ["extract"], "needs_source": True},
    }
    assert read_needs_source(cls) is True


def test_create_only_clears_source_pins():
    cls = {
        "source": "salesforce",
        "source_name": "SF 1",
        "planner_source_confirmed": True,
    }
    goal = {"operations": ["create"], "needs_source": False}
    out = clear_source_when_not_needed(cls, goal)
    assert out.get("source") is None
    assert not read_needs_source(out, goal)


def test_clear_source_when_not_needed_preserves_file_upload():
    cls = {
        "source": "file",
        "source_name": "Customer Contact Details.xlsx",
        "_file_upload_pinned": True,
        "_source_endpoint_clarified": True,
        "planner_source_entity_confirmed": True,
    }
    goal = {
        "operations": ["extract", "map", "migrate", "create"],
        "needs_source": True,
        "source_phrase": "extract sales lead data from SF 1 org",
    }
    out = clear_source_when_not_needed(cls, goal)
    assert out.get("source") == "file"
    assert out.get("source_name") == "Customer Contact Details.xlsx"
    assert out.get("_source_endpoint_clarified") is True
    assert out.get("_file_upload_pinned") is True


def test_active_job_preserves_source_pins_when_needs_source_false():
    cls = {
        "active_job_id": "job-1",
        "source": "salesforce",
        "source_name": "SF 1",
        "source_connector_id": "tok-1",
        "planner_source_confirmed": True,
    }
    goal = {"operations": ["map"], "needs_source": False}
    out = clear_source_when_not_needed(cls, goal)
    assert out.get("source") == "salesforce"
    assert out.get("source_name") == "SF 1"
    assert out.get("source_connector_id") == "tok-1"


def test_unvalidated_source_confirm_stripped():
    cls = {
        "source": "salesforce",
        "source_name": "SF 1",
        "planner_source_confirmed": True,
    }
    out = strip_unvalidated_source_confirm_flags(cls)
    assert out.get("planner_source_confirmed") is None


def test_validated_llm_pick_satisfies_slot():
    cat = _catalog()
    cls = mark_source_validated_from_llm(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_connector_id": "tok-1",
        },
    )
    goal = {"operations": ["extract"], "needs_source": True}
    assert validate_source_connector_pins(cls, cat)
    assert source_connector_slot_satisfied(cls, cat, parsed_goal=goal)
