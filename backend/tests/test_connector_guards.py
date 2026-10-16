"""Tests for connector guard helpers."""
from __future__ import annotations

from services.planning.connector_guards import (
    classification_destination_endpoint_resolved,
    classification_needs_destination_endpoint,
    classification_source_endpoint_resolved,
    classification_user_requested_destination,
)
from services.planning.destination_object_enrichment import (
    should_defer_destination_object_enrichment_early,
)


def test_cross_system_migrate_and_defer_destination_enrichment():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF Prod",
        "intent": "migrate_data",
    }
    assert should_defer_destination_object_enrichment_early(cls)
    assert not should_defer_destination_object_enrichment_early(
        {
            "source": "salesforce",
            "destination": "salesforce",
            "source_object_api_name": "Contact",
            "intent": "migrate_data",
        }
    )


def test_extract_only_sql_to_salesforce_default_does_not_need_destination():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract"],
    }
    assert not classification_user_requested_destination(cls)
    assert not classification_needs_destination_endpoint(cls)
    assert classification_destination_endpoint_resolved(cls)


def test_cross_connector_extract_with_named_dest_still_needs_destination():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF Prod",
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract", "migrate"],
    }
    assert classification_needs_destination_endpoint(cls)


def test_sql_source_resolved_with_connector_id():
    assert classification_source_endpoint_resolved(
        {
            "source": "sql",
            "source_name": "Test DB",
            "source_connector_id": "sql-1",
            "intent": "migrate_data",
        }
    )
    assert not classification_source_endpoint_resolved(
        {"source": "unknown", "destination": "salesforce", "intent": "extract"}
    )
