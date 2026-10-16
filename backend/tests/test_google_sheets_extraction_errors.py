"""Google Sheets extraction SSE error payloads."""

from agent.stream_utils import apply_sse_meta_from_line
from utils.google_sheets_errors import (
    GOOGLE_SHEETS_REAUTH_MESSAGE,
    build_google_sheets_extraction_error_payload,
    is_google_sheets_reauth_error,
)


def test_reauth_error_payload():
    exc = Exception(
        "Google Sheets refresh token expired. Please reconnect your Google Sheets account from the Connections tab."
    )
    assert is_google_sheets_reauth_error(exc)
    payload = build_google_sheets_extraction_error_payload(exc, job_id="job-1")
    assert payload["status"] == "error"
    assert payload["code"] == "google_reauth_required"
    assert payload["message"] == GOOGLE_SHEETS_REAUTH_MESSAGE
    assert payload["job_id"] == "job-1"
    assert payload["source_type"] == "google_sheets"


def test_generic_error_payload():
    exc = Exception("Spreadsheet not found")
    payload = build_google_sheets_extraction_error_payload(exc)
    assert payload["status"] == "error"
    assert "code" not in payload
    assert "Spreadsheet not found" in payload["message"]


def test_reauth_sse_sets_stream_error_meta():
    meta: dict = {}
    payload = build_google_sheets_extraction_error_payload(
        Exception("invalid_grant: Bad Request"),
        job_id="job-oauth",
    )
    import json

    apply_sse_meta_from_line(f"data: {json.dumps(payload)}\n\n", meta)
    assert meta.get("_stream_status") == "error"
    assert meta.get("_stream_error_message") == GOOGLE_SHEETS_REAUTH_MESSAGE
    assert meta.get("_pending_job_id") == "job-oauth"
