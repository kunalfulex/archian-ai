"""
Catalog disambiguation tests for gap_detector supplement path.

Slot/compiler gaps (CREATE_TITLE, DESTINATION_CONNECTOR, etc.) belong in
test_op_requirements.py and test_sf_export_new_sheet_flow.py — not here.
"""
from __future__ import annotations

import asyncio

from services.planning.gap_detector import detect_clarifications
from services.planning.resource_catalog import (
    GoogleSheetRef,
    ResourceCatalog,
    SalesforceConnectionRef,
    SqlConnectionRef,
)


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        sql_connections=[
            SqlConnectionRef(name="DB A"),
            SqlConnectionRef(name="DB B"),
        ],
        salesforce_connections=[
            SalesforceConnectionRef(name="Org One"),
            SalesforceConnectionRef(name="Org Two"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="Book1", sheet_name="Sheet1"),
            GoogleSheetRef(spreadsheet_name="Book1", sheet_name="Data"),
        ],
        has_google_sheets=True,
    )


def test_pick_salesforce_org_when_source_ambiguous():
    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "intent": "extract",
    }
    gaps = asyncio.run(
        detect_clarifications(classification, "export contacts to google sheets", _catalog())
    )
    types = {g.clarification_type for g in gaps}
    assert "pick_salesforce_org" in types


def test_pick_sql_connector():
    classification = {"source": "sql", "destination": "salesforce", "intent": "extract"}
    gaps = asyncio.run(detect_clarifications(classification, "extract from database", _catalog()))
    assert any(g.clarification_type == "pick_sql_connector" for g in gaps)


def test_extract_from_google_sheet_does_not_open_export_workbook_picker():
    """Source-only GSheets extract must not ask 'which sheet receives this export'."""
    classification = {
        "source": "google_sheets",
        "source_name": "Service Appointments",
        "destination": "unknown",
        "intent": "extract",
        "mode": "extract",
    }
    prompt = "extract 10 records from Service Appointments google sheet"
    gaps = asyncio.run(detect_clarifications(classification, prompt, _catalog()))
    assert not any(g.clarification_type == "pick_workbook" for g in gaps)


def test_sf_to_gsheets_export_still_opens_workbook_picker():
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "_source_endpoint_clarified": True,
        "destination": "google_sheets",
        "intent": "extract",
        "operations": ["extract", "map", "migrate"],
        "_pipeline_tokens": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    prompt = "extract contact data from SF 1 org and deploy them to google sheet"
    gaps = asyncio.run(detect_clarifications(classification, prompt, _catalog()))
    assert any(g.clarification_type == "pick_workbook" for g in gaps)


def test_pick_tab_when_multiple_tabs():
    classification = {
        "source": "google_sheets",
        "source_name": "Book1",
        "destination": "salesforce",
        "intent": "extract",
    }
    gaps = asyncio.run(detect_clarifications(classification, "extract from Book1", _catalog()))
    assert any(g.clarification_type == "pick_tab" for g in gaps)
