"""Tests for shared planning kernel."""
from __future__ import annotations

from services.planning.planning_kernel import (
    destination_llm_skip_reason,
    should_skip_destination_llm,
)


def test_fresh_route_skips_destination_when_confirmed():
    cls = {
        "plan_route": "fresh_path",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "planner_destination_confirmed": True,
        "destination_connector_id": "tok-1",
    }
    assert should_skip_destination_llm(cls, plan_route="fresh_path")


def test_clarification_reuses_pinned_connector():
    cls = {
        "plan_route": "fresh_path",
        "planner_pending": True,
        "destination": "google_sheets",
        "destination_name": "SF 1 XYZ list",
        "destination_spreadsheet_id": "sheet-1",
        "planner_destination_confirmed": True,
    }
    assert destination_llm_skip_reason(cls, plan_route="fresh_path") == "clarification_connector_pinned"
