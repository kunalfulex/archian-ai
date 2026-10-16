"""SSE meta parsing for agent extract → map → migrate pipelines."""

from agent.stream_utils import apply_sse_meta_from_line


def test_early_job_id_does_not_commit_until_finished():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"event_type": "extraction_job_created", "job_id": "job-early", "source_type": "sql"}\n\n',
        meta,
    )
    apply_sse_meta_from_line(
        'data: {"status": "processing", "job_id": "job-early", "source_type": "sql"}\n\n',
        meta,
    )
    assert meta.get("job_id") is None
    assert meta.get("_pending_job_id") == "job-early"
    assert meta.get("_extraction_complete") is not True


def test_extraction_job_finished_commits_job_id():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"event_type": "extraction_job_created", "job_id": "job-done"}\n\n',
        meta,
    )
    apply_sse_meta_from_line(
        'data: {"event_type": "extraction_job_finished", "job_id": "job-done", '
        '"job_status": {"status": "completed", "job_id": "job-done"}}\n\n',
        meta,
    )
    assert meta.get("job_id") == "job-done"
    assert meta.get("_extraction_complete") is True


def test_failed_extraction_marks_stream_error():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"event_type": "extraction_job_finished", "job_id": "job-fail", '
        '"job_status": {"status": "failed", "error": "Query timeout"}}\n\n',
        meta,
    )
    assert meta.get("job_id") is None
    assert meta.get("_stream_status") == "error"
    assert "timeout" in (meta.get("_stream_error_message") or "")


def test_clarification_marks_stream_clarification():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"status": "clarification", "message": "Which column should I use?"}\n\n',
        meta,
    )
    assert meta.get("_stream_status") == "clarification"
    assert "column" in (meta.get("_stream_error_message") or "")


def test_spreadsheet_created_sse_meta_includes_tab_and_event_type():
    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"status": "success", "event_type": "spreadsheet_created", '
        '"spreadsheet_name": "Sales Leads SF 1 to SF 2", '
        '"spreadsheet_id": "1kBowYmGGRy2hUV8NtvOzdxgSAbi9dMXbjMBKLK-tIwU", '
        '"sheet_name": "Sheet1", "destination": "google_sheets", '
        '"destination_name": "Sales Leads SF 1 to SF 2"}\n\n',
        meta,
    )
    assert meta.get("event_type") == "spreadsheet_created"
    assert meta.get("sheet_name") == "Sheet1"
    assert meta.get("spreadsheet_id") == "1kBowYmGGRy2hUV8NtvOzdxgSAbi9dMXbjMBKLK-tIwU"
    assert meta.get("spreadsheet_name") == "Sales Leads SF 1 to SF 2"


def test_spreadsheet_created_sse_meta_pins_entity_via_merge_result():
    from agent.session import AgentSession
    from services.mapping.context import build_mapping_execution_context

    meta: dict = {}
    apply_sse_meta_from_line(
        'data: {"status": "success", "event_type": "spreadsheet_created", '
        '"spreadsheet_name": "Sales Leads SF 1 to SF 2", '
        '"spreadsheet_id": "sid-abc", "sheet_name": "Sheet1", '
        '"destination": "google_sheets", '
        '"destination_name": "Sales Leads SF 1 to SF 2"}\n\n',
        meta,
    )
    visible = {k: v for k, v in meta.items() if not str(k).startswith("_")}
    session = AgentSession(
        user_id="u1",
        conversation_id="conv-1",
        mode="map",
        classification_snapshot={
            "destination": "google_sheets",
            "destination_name": "Sales Leads SF 1 to SF 2",
            "plan_status": "approved",
            "planner_create_spreadsheet": True,
            "active_job_id": "job-1",
        },
    )
    session.merge_result(visible)
    snap = session.classification_snapshot
    assert snap["destination_spreadsheet_id"] == "sid-abc"
    assert snap["destination_sheet_name"] == "Sheet1"
    ctx = build_mapping_execution_context(
        classification_snapshot=snap,
        destination="google_sheets",
    )
    assert ctx.has_pinned_entity
