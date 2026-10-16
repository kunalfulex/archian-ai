"""Tests for catalog phrase matching utilities."""
import asyncio

from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.gap_detector import (
    detect_clarifications,
    sanitize_unconfirmed_google_destination,
)
from services.planning.orchestrator import build_catalog_from_classify_fetch
from services.planning.resource_catalog import GoogleSheetRef, ResourceCatalog, SqlConnectionRef
from services.planning.resource_resolver import (
    match_salesforce_in_phrase,
    match_sql_connection_in_phrase,
    workbook_mentioned_as_destination,
)


def _catalog(
    sql=None,
    sf=None,
    sheets=None,
) -> ResourceCatalog:
    cat = ResourceCatalog()
    for n in sql or []:
        cat.sql_connections.append(SqlConnectionRef(name=n))
    from services.planning.resource_catalog import SalesforceConnectionRef

    for n in sf or []:
        cat.salesforce_connections.append(SalesforceConnectionRef(name=n))
    for sn, tab in sheets or []:
        cat.google_sheet_rows.append(
            GoogleSheetRef(spreadsheet_name=sn, sheet_name=tab or "Sheet1")
        )
    return cat


def test_match_salesforce_in_phrase():
    names = ["Org Alpha", "Org Beta"]
    assert match_salesforce_in_phrase("Org Beta", names) == "Org Beta"


def test_match_sql_connection_in_phrase():
    names = ["Warehouse DB", "Analytics DB"]
    assert match_sql_connection_in_phrase("Warehouse Database", names) == "Warehouse DB"


def test_workbook_mentioned_as_destination():
    assert workbook_mentioned_as_destination("contact list", "export to contact list sheet")
    assert not workbook_mentioned_as_destination("data list", "export to contact list sheet")


def test_extraction_session_continuation_pins_from_job_metadata():
    cat = _catalog(sql=["Warehouse DB", "Analytics DB"])
    extraction_meta = {
        "source_type": "sql",
        "source_name": "Warehouse DB",
        "destination": "sql",
        "destination_name": "Analytics DB",
        "job_id": "job-stale",
    }
    llm = {
        "source": "sql",
        "source_name": "Warehouse DB",
        "destination": "sql",
        "destination_name": "Analytics DB",
        "intent": "extract",
        "mode": "extract",
    }
    out = apply_extraction_session_continuation(
        llm,
        extraction_meta=extraction_meta,
        prompt="extract from Analytics DB and migrate to Warehouse DB",
        catalog=cat,
    )
    assert out["source_name"] == "Warehouse DB"
    assert out["destination_name"] == "Analytics DB"


def test_gap_detector_workbook_clarification():
    cat = _catalog(sheets=[("Book One", "Sheet1"), ("Book Two", "Sheet1")])
    classification = {
        "intent": "migrate_data",
        "source": "salesforce",
        "source_name": "My Org",
        "_source_endpoint_clarified": True,
        "destination": "google_sheets",
        "operations": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    gaps = asyncio.run(
        detect_clarifications(classification, "export customer data to google sheet", cat)
    )
    assert len(gaps) == 1
    assert gaps[0].clarification_type == "pick_workbook"


def test_build_catalog_from_classify_fetch():
    cat = build_catalog_from_classify_fetch(
        sql_connection_names=["DB1"],
        salesforce_connections_json={"connections": [{"name": "SF1", "is_default": True}]},
        google_sheet_rows=[{"spreadsheet_name": "WB", "sheet_name": "Tab1"}],
        has_google_sheets=True,
    )
    assert cat.sql_names == ["DB1"]
    assert cat.salesforce_names == ["SF1"]
    assert cat.spreadsheet_names == ["WB"]
