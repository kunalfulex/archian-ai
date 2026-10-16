"""Tests for planning activity bus and SSE drain."""
from __future__ import annotations

import pytest

from services.planning.planning_activity import (
    PlanningActivityBus,
    drain_planning_activity_sse_lines,
    emit_planning_activity,
    format_operations_detail,
    format_source_detail,
    install_planning_activity_bus,
    reset_planning_activity_bus,
    terminal_planning_failure_sse_lines,
    terminal_planning_success_sse_lines,
)


def test_emit_and_drain_sse_lines():
    token = install_planning_activity_bus()
    try:
        emit_planning_activity("parse_prompt", "Understanding your request", "running", segment="scope")
        emit_planning_activity("parse_prompt", "Understanding your request", "done", segment="scope")
        lines = drain_planning_activity_sse_lines()
        assert len(lines) == 2
        assert "planning_activity" in lines[0]
        assert "Understanding your request" in lines[0]
        assert drain_planning_activity_sse_lines() == []
    finally:
        reset_planning_activity_bus(token)


def test_emit_without_bus_is_noop():
    emit_planning_activity("parse_prompt", "Understanding your request", "running")
    assert drain_planning_activity_sse_lines() == []


def test_format_helpers():
    detail = format_source_detail({"source": "salesforce", "source_name": "Org 2"})
    assert detail == "Salesforce (Org 2)"
    ops = format_operations_detail({"operations": ["extract", "filter", "migrate"]})
    assert ops == "extract, filter, migrate"


def test_terminal_planning_success_sse_lines():
    from types import SimpleNamespace

    plan = SimpleNamespace(
        steps=[
            SimpleNamespace(label="Extract Contact", tool_id="extract_salesforce"),
            SimpleNamespace(label="Migrate", tool_id="start_migration"),
        ]
    )
    lines = terminal_planning_success_sse_lines(plan)
    assert len(lines) >= 4
    assert any("Plan ready (2 steps)" in line for line in lines)
    assert all("planning_activity" in line for line in lines)
    assert not any('"status": "error"' in line and "planning_activity" in line for line in lines)


def test_terminal_planning_failure_uses_failed_status():
    lines = terminal_planning_failure_sse_lines()
    assert any('"status": "failed"' in line for line in lines)
    assert not any(
        line.count('"status": "error"') and "plan_failed" in line for line in lines
    )
