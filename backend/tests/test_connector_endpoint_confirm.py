"""Tests for connector instance confirmation (source/destination org/DB pickers)."""
from __future__ import annotations

import asyncio

from services.planning.connector_endpoint_confirm import (
    destination_endpoint_is_confirmed,
    sanitize_unconfirmed_connector_endpoints_catalog,
    sanitize_unconfirmed_destination_endpoint_catalog,
    source_endpoint_is_confirmed,
)
from services.planning.gap_detector import detect_clarifications
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef, SqlConnectionRef


def _multi_sf_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ],
        google_sheet_rows=[],
    )


def test_catalog_sanitize_clears_inferred_sf_destination_name():
    cat = _multi_sf_catalog()
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "needs_destination": True},
    }
    out = sanitize_unconfirmed_destination_endpoint_catalog(cls, cat)
    assert out["destination"] == "salesforce"
    assert out.get("destination_name") is None


def test_explicit_sf_destination_name_kept_when_clarified():
    cat = _multi_sf_catalog()
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 2",
        "intent": "migrate_data",
        "_destination_endpoint_clarified": True,
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "needs_destination": True},
    }
    prompt = "migrate user data to SF 2"
    assert destination_endpoint_is_confirmed(cls, prompt, cat)
    out = sanitize_unconfirmed_connector_endpoints_catalog(cls, cat)
    assert out["destination_name"] == "SF 2"


def test_extract_only_no_destination_picker():
    cat = _multi_sf_catalog()
    cls = {
        "source": "sql",
        "destination": "unknown",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {"operations": ["extract"], "needs_destination": False},
    }
    gaps = asyncio.run(detect_clarifications(cls, "extract users from Test DB", cat))
    types = {g.clarification_type for g in gaps}
    assert "pick_salesforce_org" not in types


def test_source_explicit_in_prompt_confirmed():
    cat = _multi_sf_catalog()
    cls = {"source": "sql", "source_name": "Test DB", "intent": "extract"}
    assert source_endpoint_is_confirmed(cls, "extract from Test DB", cat)
