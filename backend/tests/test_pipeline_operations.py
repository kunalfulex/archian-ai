"""Tests for pipeline operations foundation (P0 + P1)."""
from __future__ import annotations

import uuid

from services.intent.gaps import _clarification_from_plan
from services.planning.connector_guards import classification_needs_source_endpoint
from services.planning.create_metadata import apply_create_metadata_to_classification
from services.planning.operation_resolver import (
    normalize_operation,
    resolve_tool_id,
    supports_operation,
)
from services.planning.plan_builder import build_plan_steps
from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps
from services.planning.schemas import Clarification


def test_clarification_from_plan_dedupes_by_type():
    clar = Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_source_connector",
        prompt="Where to extract from?",
        options=[],
        required=True,
    )
    classification = {
        "clarifications": [clar.model_dump(mode="json")],
        "execution_plan": {
            "clarifications": [
                {
                    "id": str(uuid.uuid4()),
                    "clarification_type": "pick_source_connector",
                    "prompt": "Where to extract from? (duplicate)",
                    "options": [],
                    "required": True,
                }
            ]
        },
    }
    out = _clarification_from_plan(classification)
    assert len(out) == 1
    assert out[0].clarification_type == "pick_source_connector"


def test_standalone_create_does_not_need_source_endpoint():
    cls = {
        "intent": "create",
        "_plan_scope": {"operations": ["create"]},
        "_pipeline_tokens": ["create"],
    }
    assert classification_needs_source_endpoint(cls) is False
    assert classification_needs_source_endpoint(cls, pipeline_tokens=["create"]) is False


def test_resolve_pipeline_steps_create_only():
    goal = {"operations": ["create"]}
    ctx = PlanContext()
    assert resolve_pipeline_steps(goal, ctx) == ["create"]


def test_standalone_create_plan_scope_driven():
    prompt = "Create a new google sheet for Sales Contacts with headers Name, Email"
    classification = apply_create_metadata_to_classification(
        {
            "destination": "google_sheets",
            "intent": "create",
            "mode": "create",
            "operations": ["create"],
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Sales Contacts",
        },
        {
            "operations": ["create"],
            "intent": "create",
            "create_destination": True,
            "spreadsheet_title": "Sales Contacts",
            "spreadsheet_headers": ["Name", "Email"],
        },
    )
    classification["_plan_scope"] = {
        "operations": ["create"],
        "intent": "create",
        "create_destination": True,
        "spreadsheet_title": "Sales Contacts",
        "spreadsheet_headers": ["Name", "Email"],
    }
    plan = build_plan_steps(classification, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["google_sheets_create_from_prompt"]
    assert "create" in (plan.metadata.get("pipeline_steps") or [])


def test_operation_resolver_matrix():
    assert normalize_operation("deploy") == "deploy_schema"
    assert resolve_tool_id("extract", "salesforce") == "extract_salesforce"
    assert resolve_tool_id("extract", "sql") == "extract_sql"
    assert resolve_tool_id("create", "google_sheets") == "google_sheets_create_from_prompt"
    assert resolve_tool_id("deploy_schema", "salesforce") == "metadata_deploy_salesforce"
    assert resolve_tool_id("join", "google_sheets") == "gsheets_execute_operation"
    assert resolve_tool_id("upsert", "google_sheets") == "gsheets_execute_operation"
    assert resolve_tool_id("search", "google_sheets") == "gsheets_execute_operation"
    assert resolve_tool_id("formula", "google_sheets") == "gsheets_execute_operation"
    assert resolve_tool_id("update", "google_sheets") == "google_sheets_update_metadata"
    assert supports_operation("google_sheets", "create") is True
    assert supports_operation("salesforce", "create") is False


def test_standalone_upsert_does_not_need_source_endpoint():
    cls = {
        "intent": "upsert",
        "_plan_scope": {"operations": ["upsert"]},
        "_pipeline_tokens": ["upsert"],
    }
    assert classification_needs_source_endpoint(cls) is False


def test_resolve_pipeline_steps_upsert_only():
    goal = {"operations": ["upsert"]}
    ctx = PlanContext()
    assert resolve_pipeline_steps(goal, ctx) == ["upsert"]


def test_standalone_upsert_plan_scope_driven():
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "destination_name": "Leads",
        "intent": "upsert",
        "_plan_scope": {"operations": ["upsert"], "intent": "upsert"},
    }
    plan = build_plan_steps(classification, prompt="upsert row to Leads sheet")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["gsheets_execute_operation"]
    assert plan.steps[0].parameters.get("operation") == "upsert"
