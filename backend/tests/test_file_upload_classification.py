"""Tests for in-chat file upload source pinning before planner gaps."""
from __future__ import annotations

from services.file_extraction.pending_upload import (
    is_pinned_file_upload,
    pin_file_upload_on_classification,
)
from services.intent.gaps import _needs_source_connector
from services.intent.state import IntentState
from services.planning.connector_guards import classification_source_endpoint_resolved
from services.planning.endpoint_resolver import resolve_endpoints
from services.planning.gap_detector import _source_connector_type_clarification
from services.planning.resource_catalog import ResourceCatalog


def test_pin_file_upload_sets_source_and_clears_connector_picker():
    out = pin_file_upload_on_classification(
        {
            "source": "unknown",
            "destination": "salesforce",
            "intent": "migrate_data",
            "needs_clarification": True,
            "clarifications": [
                {"clarification_type": "pick_source_connector", "prompt": "pick one"}
            ],
        },
        filename="Customer Contact Details.xlsx",
    )
    assert out["source"] == "file"
    assert out["source_name"] == "Customer Contact Details.xlsx"
    assert out["_file_upload_pinned"] is True
    assert out["_endpoints_resolved"] is True
    assert out.get("needs_clarification") is False
    assert is_pinned_file_upload(out)


def test_classification_source_endpoint_resolved_for_file_upload():
    assert classification_source_endpoint_resolved(
        {
            "source": "file",
            "source_name": "contacts.xlsx",
            "intent": "migrate_data",
        }
    )


def test_needs_source_connector_skips_pinned_file():
    state = IntentState(prompt="migrate to salesforce")
    state.source_type = "file"
    assert not _needs_source_connector(
        state,
        {"source": "file", "source_name": "contacts.xlsx", "_file_upload_pinned": True},
    )


def test_gap_detector_no_pick_source_connector_for_file_upload():
    cat = ResourceCatalog(
        sql_connections=[],
        salesforce_connections=[],
        google_sheet_rows=[],
        has_google_sheets=False,
    )
    clar = _source_connector_type_clarification(
        pin_file_upload_on_classification(
            {"source": "unknown", "intent": "migrate_data"},
            filename="data.xlsx",
        ),
        cat,
    )
    assert clar is None


def test_resolve_endpoints_keeps_file_source_on_migrate_prompt():
    cat = ResourceCatalog(
        sql_connections=[],
        salesforce_connections=[],
        google_sheet_rows=[],
        has_google_sheets=False,
    )
    cls = pin_file_upload_on_classification(
        {"source": "file", "source_name": "contacts.xlsx", "intent": "migrate_data"},
        filename="contacts.xlsx",
    )
    resolution = resolve_endpoints(
        "migrate them to salesforce org SF 1",
        cat,
        classification=cls,
    )
    assert resolution.source is not None
    assert resolution.source.connector_type == "file"
    assert resolution.source.display_name == "contacts.xlsx"
