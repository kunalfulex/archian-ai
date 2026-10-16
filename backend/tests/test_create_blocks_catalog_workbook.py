"""Create in PLAN_SCOPE must not bind catalog workbooks (new sheet only)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.connector_llm_resolver import (
    LlmConnectorPick,
    apply_destination_connector_pick,
)
from services.planning.create_metadata import (
    classification_requests_new_spreadsheet,
    clear_catalog_workbook_pins_for_create,
)
from services.planning.destination_llm_resolve import resolve_destination_connector_via_llm
from services.planning.google_sheets_destination import apply_google_sheets_workbook_match
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        salesforce_connections=[SalesforceConnectionRef(name="SF 1", token_id="tok-1")],
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-1", name="Archian AI"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="SF 1 Contact list",
                sheet_name="Sheet1",
                spreadsheet_id="sid-contact",
                google_sheets_connection_id="conn-1",
            ),
        ],
    )


def test_classification_requests_new_spreadsheet_when_create_in_ops():
    cls = {"destination": "google_sheets", "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]}}
    assert classification_requests_new_spreadsheet(cls) is True


def test_workbook_picker_done_disables_create_path():
    cls = {
        "planner_create_spreadsheet": True,
        "planner_workbook_picker_done": True,
        "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]},
    }
    assert classification_requests_new_spreadsheet(cls) is False


def test_clear_catalog_workbook_pins_removes_existing_sheet_ids():
    cls = {
        "destination": "google_sheets",
        "destination_name": "SF 1 Contact list",
        "destination_spreadsheet_id": "sid-contact",
        "destination_sheet_name": "Sheet1",
        "planner_create_spreadsheet": True,
        "_destination_connector_validated": True,
        "planner_destination_confirmed": True,
        "_plan_scope": {"operations": ["extract", "create", "map", "migrate"], "needs_destination": True},
    }
    out = clear_catalog_workbook_pins_for_create(cls)
    assert out.get("destination_spreadsheet_id") is None
    assert out.get("destination_name") is None
    assert out.get("planner_create_spreadsheet") is True
    assert not out.get("_destination_connector_validated")


def test_apply_workbook_match_noop_on_create_path():
    cat = _catalog()
    cls = {
        "planner_create_spreadsheet": True,
        "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]},
    }
    out = apply_google_sheets_workbook_match(
        cls,
        {
            "spreadsheet_name": "SF 1 Contact list",
            "spreadsheet_id": "sid-contact",
            "sheet_name": "Sheet1",
        },
        catalog=cat,
    )
    assert out.get("destination_spreadsheet_id") is None
    assert not out.get("_destination_connector_validated")


def test_connector_pick_on_create_path_pins_account_only():
    cat = _catalog()
    cls = {
        "planner_create_spreadsheet": True,
        "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]},
    }
    out = apply_destination_connector_pick(
        cls,
        LlmConnectorPick("google_sheets", "Archian AI", "conn-1", 0.9),
        cat,
    )
    assert out.get("destination_spreadsheet_id") is None
    assert out.get("google_sheets_connection_id") == "conn-1"


def test_destination_llm_resolver_skips_connector_llm_when_create():
    cat = _catalog()
    goal = {"operations": ["extract", "create", "map", "migrate"], "needs_destination": True}
    cls = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "google_sheets",
        "planner_create_spreadsheet": True,
        "_plan_scope": goal,
    }

    with patch(
        "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
        new_callable=AsyncMock,
        return_value=LlmConnectorPick("google_sheets", "Archian AI", "conn-1", 0.9),
    ) as mock_llm:
        out = asyncio.run(
            resolve_destination_connector_via_llm(
                cls,
                prompt="export to new google sheet",
                parsed_goal=goal,
                catalog=cat,
                user_id="user-1",
            )
        )
    mock_llm.assert_not_called()
    assert out.get("destination_spreadsheet_id") is None
    assert out.get("planner_create_spreadsheet") is True
