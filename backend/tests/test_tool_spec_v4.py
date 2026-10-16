"""Tests for Agent Runtime v4 Phase 0 — ToolSpec, risk gate, tool traces."""
from __future__ import annotations

from agent.registry import default_registry
from agent.risk_gate import approval_granted, check_risk_gate, requires_approval
from agent.session import AgentSession
from agent.tool_spec import spec_for_tool
from services.pipeline_learning import chunk_builder


def test_all_registered_tools_have_spec():
    reg = default_registry()
    for tool in reg._tools.values():
        spec = tool.spec()
        assert spec.tool_id == tool.tool_id
        assert spec.description
        assert spec.risk_tier in ("read", "transform", "write", "deploy")


def test_write_tools_require_approval():
    reg = default_registry()
    map_tool = reg._tools["map_fields"]
    assert requires_approval(map_tool.spec()) is True
    read_tool = reg._tools["retrieve_similar_pipelines"]
    assert requires_approval(read_tool.spec()) is False


def test_risk_gate_blocks_write_without_approval():
    reg = default_registry()
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="map",
        active_job_id="job-1",
    )
    tool = reg._tools["map_fields"]
    allowed, reason = check_risk_gate(tool, session, {"job_id": "job-1"})
    assert allowed is False
    assert reason


def test_risk_gate_allows_write_with_recovery_approval():
    reg = default_registry()
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        active_job_id="job-1",
        classification_snapshot={"recovery_approved": True},
    )
    tool = reg._tools["map_fields"]
    allowed, _ = check_risk_gate(tool, session, {"job_id": "job-1"})
    assert allowed is True
    assert approval_granted(session, "map_fields") is True


def test_build_tool_trace_text():
    text = chunk_builder.build_tool_trace_text(
        prompt="filter invalid emails then dedupe",
        steps=[
            {"tool_id": "run_data_ops_plan", "ok": True},
            {"tool_id": "map_fields", "ok": True},
        ],
        source_type="google_sheets",
        destination_type="salesforce",
        mode="data_ops",
    )
    assert "tools:run_data_ops_plan -> map_fields" in text
    assert "goal:filter invalid emails" in text


def test_spec_for_tool_transform_extract():
    reg = default_registry()
    spec = spec_for_tool(reg._tools["extract_sql"])
    assert spec.risk_tier == "transform"
    assert "job_id" in spec.produces
