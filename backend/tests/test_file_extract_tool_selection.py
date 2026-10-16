"""File extract tool id selection for planner plans."""
from __future__ import annotations

from services.planning.plan_builder import _extract_tool


def test_extract_tool_uses_extract_file_when_pending_payload():
    tool = _extract_tool(
        "file",
        classification={"_pending_file_payload": {"filename": "a.csv", "content": b"x"}},
        prompt="extract contacts",
    )
    assert tool == "extract_file"


def test_extract_tool_uses_reextract_when_active_job_and_no_fresh_pipeline():
    tool = _extract_tool(
        "file",
        classification={"active_job_id": "job-1", "source": "file"},
        prompt="map to salesforce",
    )
    assert tool == "file_reextract"
