"""Multi-account Google Sheets destination: account picker before workbook."""
from __future__ import annotations

import asyncio

from services.planning.clarification_apply import apply_clarification_selections
from services.planning.destination_catalog_validate import (
    destination_connector_slot_satisfied,
    needs_google_sheets_account_pick,
)
from services.planning.gap_detector import (
    _build_pick_google_account_clarification,
    _build_pick_workbook_clarification,
    detect_clarifications,
    resolve_destination_connector_clarification,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def _multi_account_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-a", name="Work Account"),
            GoogleSheetsConnectionRef(connection_id="conn-b", name="Personal Account"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                spreadsheet_id="sid-a",
                google_sheets_connection_id="conn-a",
                account_name="Work Account",
            ),
            GoogleSheetRef(
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                spreadsheet_id="sid-b",
                google_sheets_connection_id="conn-b",
                account_name="Personal Account",
            ),
        ],
    )


def test_needs_google_account_when_multiple_connections():
    cat = _multi_account_catalog()
    cls = {"destination": "google_sheets", "intent": "migrate_data"}
    assert needs_google_sheets_account_pick(cls, cat) is True


def test_account_picker_before_workbook():
    cat = _multi_account_catalog()
    cls = {
        "destination": "google_sheets",
        "source": "salesforce",
        "source_name": "SF 1",
        "_source_endpoint_clarified": True,
        "planner_source_entity_confirmed": True,
        "intent": "extract",
        "operations": ["extract", "map", "migrate"],
        "_pipeline_tokens": ["extract", "map", "migrate"],
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "needs_destination": True},
    }
    prompt = "export contacts to google sheet"
    gaps = asyncio.run(detect_clarifications(cls, prompt, cat))
    types = [g.clarification_type for g in gaps]
    assert "pick_google_account" in types
    assert "pick_workbook" not in types


def test_workbook_picker_scoped_after_account_pick():
    cat = _multi_account_catalog()
    cls = {
        "destination": "google_sheets",
        "google_sheets_connection_id": "conn-a",
        "destination_connector_id": "conn-a",
        "destination_account_name": "Work Account",
    }
    assert needs_google_sheets_account_pick(cls, cat) is False
    clar = _build_pick_workbook_clarification(cls, "export to sheet", cat)
    assert clar is not None
    assert len(clar.options) == 2  # one workbook + create new
    labels = [o.label for o in clar.options if not o.create_new]
    assert len(labels) == 1
    assert "Work Account" in labels[0]


def test_apply_account_then_workbook_validates():
    cat = _multi_account_catalog()
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets"}, cat
    )
    assert account_clar is not None
    cls = {
        "destination": "google_sheets",
        "_planning_catalog": cat,
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "needs_destination": True},
    }
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
    )
    assert cls["google_sheets_connection_id"] == "conn-a"
    assert cls.get("_destination_endpoint_clarified") is True
    assert destination_connector_slot_satisfied(cls, cat)

    workbook_clar = _build_pick_workbook_clarification(cls, "export", cat)
    assert workbook_clar is not None
    opt = next(o for o in workbook_clar.options if not o.create_new)
    plan.clarifications = [workbook_clar]
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": workbook_clar.id, "option_id": opt.id}],
    )
    assert cls["destination_spreadsheet_id"] == "sid-a"
    assert destination_connector_slot_satisfied(cls, cat)


def test_resolve_destination_prefers_account_clarification():
    cat = _multi_account_catalog()
    cls = {"destination": "google_sheets"}
    clar = resolve_destination_connector_clarification(cls, cat, "deploy to google sheet")
    assert clar is not None
    assert clar.clarification_type == "pick_google_account"
