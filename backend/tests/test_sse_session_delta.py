"""SSE wire protocol vs session delta — no lifecycle fields in merge_result."""
from __future__ import annotations

from agent.stream_utils import apply_sse_meta_from_line, session_delta_from_sse_meta


def test_session_delta_omits_event_type_from_extraction_finished():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"event_type": "extraction_job_finished", "job_id": "job-1", '
        '"job_status": {"status": "completed", "job_id": "job-1", '
        '"result": {"record_count": 178}}}\n\n',
        meta,
    )
    delta = session_delta_from_sse_meta(meta)
    assert delta.get("job_id") == "job-1"
    assert "event_type" not in delta
    assert "job_status" not in delta
    assert meta.get("_extraction_finished_sse_emitted") is True


def test_session_delta_keeps_spreadsheet_created_fields():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"status": "success", "event_type": "spreadsheet_created", '
        '"spreadsheet_id": "sid-1", "sheet_name": "Sheet1", '
        '"destination": "google_sheets"}\n\n',
        meta,
    )
    delta = session_delta_from_sse_meta(meta)
    assert delta.get("spreadsheet_id") == "sid-1"
    assert delta.get("destination") == "google_sheets"
    assert "event_type" not in delta


def test_legacy_terminal_result_envelope_is_hidden_shape():
    """Document the leaked payload shape — must not contain user-facing fields."""
    payload = {
        "status": "success",
        "job_id": "job-1",
        "result": {"event_type": "extraction_job_finished", "job_id": "job-1"},
    }
    delta_keys = set(payload.get("result") or {})
    assert "event_type" in delta_keys
    assert "data" not in delta_keys
    assert "message" not in delta_keys
