"""Tests for Google Sheets catalog phrase matching (no prompt routing)."""

from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.orchestrator import apply_planner_post_classification
from services.planning.resource_catalog import GoogleSheetRef, ResourceCatalog


def test_same_workbook_preserves_llm_destination_without_mechanical_override():
    """Pre-planner hook must not rewrite destination from session history."""
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "gsheets",
                "destination": "unknown",
                "source_name": "contact list",
                "job_id": "job-1",
            }
        }
    ]
    cls = {
        "source": "gsheets",
        "destination": "salesforce",
        "destination_name": "Contact",
        "intent": "map_data",
        "mode": "map",
        "destination_same_as_source": True,
    }
    from services.intent.connector_enrichment import apply_pre_planner_connector_hooks

    out = apply_pre_planner_connector_hooks(
        cls,
        prompt="start the mapping to same sheet",
        conversation_history=history,
    )
    assert out["destination"] == "salesforce"
    assert out["destination_name"] == "Contact"
    assert out["destination_same_as_source"] is True


def test_gsheets_continuation_keeps_job_pins():
    cat = ResourceCatalog(
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="Customer Contact Details", sheet_name="Sheet1"),
            GoogleSheetRef(spreadsheet_name="contact list", sheet_name="Sheet1"),
        ],
        has_google_sheets=True,
    )
    prompt = (
        "can you extract data from contact list sheet "
        "and migrate them to Customer Contact Details sheet"
    )
    extraction_meta = {
        "source_type": "google_sheets",
        "source_name": "Customer Contact Details",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "job_id": "job-stale",
    }
    llm = {
        "source": "google_sheets",
        "source_name": "Customer Contact Details",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "intent": "extract",
        "mode": "extract",
        "session_action": "fresh_extract",
    }
    out = apply_planner_post_classification(
        llm,
        prompt=prompt,
        catalog=cat,
        extraction_meta=extraction_meta,
    )
    assert out["source_name"] == "Customer Contact Details"
    assert out["destination_name"] == "contact list"
    assert "active_job_id" not in out


def test_extraction_session_continuation_same_workbook():
    cat = ResourceCatalog(
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="contact list", sheet_name="Sheet1"),
        ],
        has_google_sheets=True,
    )
    out = apply_extraction_session_continuation(
        {"intent": "map_data", "mode": "map", "destination": "unknown"},
        extraction_meta={
            "source_type": "google_sheets",
            "source_name": "contact list",
            "destination": "google_sheets",
            "destination_name": "contact list",
            "job_id": "job-1",
        },
        prompt="map to the same sheet",
        catalog=cat,
    )
    assert out["source_name"] == "contact list"
