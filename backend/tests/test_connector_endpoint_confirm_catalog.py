"""Catalog-only connector endpoint sanitize (PLAN_SCOPE path — no prompt keywords)."""
from __future__ import annotations

from services.planning.connector_endpoint_confirm import (
    sanitize_unconfirmed_connector_endpoints,
    sanitize_unconfirmed_connector_endpoints_catalog,
    sanitize_unconfirmed_destination_endpoint_catalog,
)
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef, SqlConnectionRef


def _multi_sf_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )


def test_catalog_sanitize_clears_inferred_sf_destination_without_authoritative_flags():
    cat = _multi_sf_catalog()
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    out = sanitize_unconfirmed_destination_endpoint_catalog(cls, cat)
    assert out.get("destination_name") is None
    assert not out.get("planner_destination_confirmed")


def test_catalog_sanitize_keeps_clarified_destination():
    cat = _multi_sf_catalog()
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 2",
        "_destination_endpoint_clarified": True,
        "planner_destination_confirmed": True,
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_destination": True,
        },
    }
    out = sanitize_unconfirmed_destination_endpoint_catalog(cls, cat)
    assert out["destination_name"] == "SF 2"


def test_plan_scope_routes_to_catalog_sanitize_not_prompt():
    cat = ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ],
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "needs_destination": True},
    }
    prompt = "deploy user data to salesforce"
    out = sanitize_unconfirmed_connector_endpoints(cls, prompt, cat)
    assert out.get("destination_name") is None


def test_catalog_bundle_clears_multi_sql_source_without_clarification():
    cat = ResourceCatalog(
        sql_connections=[
            SqlConnectionRef(name="Warehouse DB", connector_id="w"),
            SqlConnectionRef(name="Analytics DB", connector_id="a"),
        ]
    )
    cls = {
        "source": "sql",
        "source_name": "Warehouse DB",
        "intent": "extract",
        "_plan_scope": {"operations": ["extract"], "needs_destination": False},
    }
    out = sanitize_unconfirmed_connector_endpoints_catalog(cls, cat)
    assert out.get("source_name") is None
