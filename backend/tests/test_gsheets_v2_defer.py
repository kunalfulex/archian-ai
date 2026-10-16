"""Tests for plan-scope deferral and micro-plan skip."""
from __future__ import annotations

import asyncio

from agent.planner import Planner
from agent.session import AgentSession
from services.planning.planner_path import classification_has_plan_scope


def test_classification_has_plan_scope_when_plan_scope_present():
    assert classification_has_plan_scope({"_plan_scope": {"operations": ["upsert"]}}) is True


def test_classification_has_plan_scope_when_execution_plan_has_steps():
    assert classification_has_plan_scope(
        {"execution_plan": {"steps": [{"parameters": {"tool_id": "extract_sql"}}]}}
    ) is True


def test_classification_has_plan_scope_when_plan_status_awaiting_approval():
    assert classification_has_plan_scope(
        {"source": "google_sheets", "plan_status": "awaiting_approval"}
    ) is True


def test_classifier_ops_alone_is_not_plan_scope():
    """Classifier operations[] alone is not plan scope — orchestrator must run first."""
    assert classification_has_plan_scope(
        {"source": "google_sheets", "operations": ["upsert"]}
    ) is False


def test_legacy_gsheets_classification_without_scope_is_not_plan_scope():
    assert classification_has_plan_scope(
        {"source": "google_sheets", "intent": "general"}
    ) is False


def test_micro_plan_skipped_for_standalone_upsert_ops():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={
            "source": "google_sheets",
            "destination": "google_sheets",
            "destination_name": "Leads",
            "intent": "upsert",
            "operations": ["upsert"],
            "_plan_scope": {"operations": ["upsert"]},
            "execution_plan": {
                "status": "approved",
                "steps": [
                    {
                        "step_type": "upsert",
                        "parameters": {
                            "tool_id": "gsheets_execute_operation",
                            "operation": "upsert",
                            "step_id": "upsert",
                        },
                    }
                ],
            },
            "plan_status": "approved",
        },
        prompt="upsert row into Leads",
    )
    plan = asyncio.run(Planner().build_plan("upsert row into Leads", session))
    tools = [s.tool_id for s in plan.steps]
    assert tools == ["gsheets_execute_operation"]


def test_micro_plan_skipped_for_sf_export_to_gsheets():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "destination": "google_sheets",
            "source_name": "Prod",
            "intent": "extract",
            "operations": ["extract", "create", "map", "migrate"],
            "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]},
            "execution_plan": {
                "status": "approved",
                "steps": [
                    {
                        "step_type": "extract",
                        "parameters": {"tool_id": "extract_salesforce", "step_id": "extract"},
                    }
                ],
            },
            "plan_status": "approved",
        },
        prompt="export contacts to new sheet",
    )
    plan = asyncio.run(Planner().build_plan("export contacts to new sheet", session))
    assert "gsheets_resolve_context" not in [s.tool_id for s in plan.steps]
