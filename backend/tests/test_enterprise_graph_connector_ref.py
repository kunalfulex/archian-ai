"""Tests for enterprise graph connector ref normalization (P3 GSheets)."""
from services.enterprise_graph.connector_ref import (
    assessment_connector_ref,
    sheet_entity_allowlist_from_ref,
)


def test_assessment_connector_ref_sheets_includes_connection_id():
    ref = assessment_connector_ref(
        "google_sheets",
        {"google_sheets_connection_id": "conn-1"},
    )
    assert ref["google_sheets_connection_id"] == "conn-1"
    assert ref["connection_id"] == "conn-1"


def test_sheet_entity_allowlist_from_ref():
    assert sheet_entity_allowlist_from_ref({"sheet_entity_allowlist": ["sid::Sheet1"]}) == [
        "sid::Sheet1"
    ]
    assert sheet_entity_allowlist_from_ref({}) is None
    assert sheet_entity_allowlist_from_ref({"sheet_entity_allowlist": []}) == []
    assert sheet_entity_allowlist_from_ref({"sheet_entity_allowlist": ["", "  "]}) is None
