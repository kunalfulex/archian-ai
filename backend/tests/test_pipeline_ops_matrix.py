"""Automated coverage for pipeline operations test matrix."""
from __future__ import annotations

import asyncio
import uuid

from services.intent.gaps import _clarification_from_plan
from services.intent.plan_assembly import assemble_migration_plan_tail
from services.planning.binding.pipeline import run_plan_binding_pipeline
from services.planning.connector_guards import classification_needs_source_endpoint
from services.planning.create_metadata import apply_create_metadata_to_classification
from services.planning.op_requirements import (
    JOIN_SPEC,
    SOURCE_ENTITY,
    read_plan_requirements,
    requirements_need_source_bind,
)
from services.planning.plan_builder import build_plan_steps
from services.planning.pipeline_step_resolver import PlanContext, resolve_pipeline_steps
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import Clarification, ExecutionPlan
from tests.plan_scope_helpers import assert_pipeline_steps, with_plan_scope


def _clar_types(classification: dict) -> list:
    return [c.clarification_type for c in _clarification_from_plan(classification)]


def test_matrix_create_only_no_source_picker():
    classification = with_plan_scope(
        {
            "destination": "google_sheets",
            "intent": "create",
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "Sales Contacts",
            "planner_spreadsheet_headers": ["Name", "Email"],
        },
        operations=["create"],
        create_destination=True,
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["create"],
    )
    reqs = read_plan_requirements(classification)
    assert SOURCE_ENTITY not in {s.slot for s in reqs.all_slots()}
    assert not requirements_need_source_bind(reqs)
    assert classification_needs_source_endpoint(classification) is False
    plan = build_plan_steps(classification, prompt="create sheet with Name, Email")
    assert_pipeline_steps(plan, ["create"])
    assert "pick_source_connector" not in _clar_types({"execution_plan": plan.model_dump()})


def test_matrix_create_only_orchestrator_path():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Sales Contacts",
        "spreadsheet_headers": ["Name", "Email"],
    }
    classification = apply_create_metadata_to_classification(
        {
            "destination": "google_sheets",
            "intent": "create",
            "plan_route": "fresh_path",
            "planner_spreadsheet_title_confirmed": True,
            "_destination_endpoint_clarified": True,
        },
        goal,
    )
    classification["planner_new_spreadsheet_name"] = goal["spreadsheet_title"]
    classification = sync_scope_into_classification(classification, goal, ["create"])
    plan = ExecutionPlan(plan_id=str(uuid.uuid4()), status="draft")
    catalog = ResourceCatalog()

    out, plan, _, _ = asyncio.run(
        run_plan_binding_pipeline(
            classification,
            plan,
            user_id="u1",
            prompt="create Sales Contacts sheet",
            parsed_goal=goal,
            catalog=catalog,
        )
    )
    out, plan, _, _ = asyncio.run(
        assemble_migration_plan_tail(
            out,
            plan,
            user_id="u1",
            prompt="create Sales Contacts sheet",
            parsed_goal=goal,
            catalog=catalog,
            migration_intent=None,
        )
    )
    assert len(plan.steps) == 1
    assert plan.steps[0].parameters.get("tool_id") == "google_sheets_create_from_prompt"


def test_matrix_sf_extract_new_gsheet():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "source_name": "SF 1",
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "CRM",
            "planner_destination_confirmed": True,
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
    )
    plan = build_plan_steps(classification, prompt="extract and export to new sheet")
    assert_pipeline_steps(plan, ["extract", "create", "map", "migrate"])


def test_matrix_standalone_upsert_no_source():
    classification = with_plan_scope(
        {
            "source": "google_sheets",
            "destination": "google_sheets",
            "destination_name": "Leads",
            "intent": "upsert",
        },
        operations=["upsert"],
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["upsert"],
    )
    reqs = read_plan_requirements(classification)
    assert SOURCE_ENTITY not in {s.slot for s in reqs.all_slots()}
    assert classification_needs_source_endpoint(classification) is False
    plan = build_plan_steps(classification, prompt="upsert row")
    assert_pipeline_steps(plan, ["upsert"])


def test_matrix_join_multi_source():
    classification = with_plan_scope(
        {
            "source": "google_sheets",
            "source_name": "Book",
            "source_sheet_name": "Tab1",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "Book", "sheet_name": "Tab2"}
            ],
            "multi_source_pipeline": True,
            "intent": "extract",
        },
        operations=["extract", "join"],
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "join"],
    )
    reqs = read_plan_requirements(classification)
    assert JOIN_SPEC in {s.slot for s in reqs.all_slots()}
    plan = build_plan_steps(classification, prompt="join tabs in Book")
    assert_pipeline_steps(plan, ["extract", "join"])


def test_matrix_deploy_no_extract_source():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "destination": "salesforce",
            "source_name": "Prod",
            "destination_name": "Sandbox",
            "intent": "metadata_deploy",
        },
        operations=["deploy_schema"],
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["deploy_schema"],
    )
    reqs = read_plan_requirements(classification)
    assert SOURCE_ENTITY not in {s.slot for s in reqs.all_slots()}
    assert classification_needs_source_endpoint(classification) is True
    plan = build_plan_steps(classification, prompt="deploy record types")
    assert_pipeline_steps(plan, ["deploy_schema"])


def test_matrix_extract_sql_only():
    goal = {"operations": ["extract"]}
    ctx = PlanContext()
    assert resolve_pipeline_steps(goal, ctx) == ["extract"]
    classification = with_plan_scope(
        {"source": "sql", "source_name": "Warehouse", "destination": "unknown", "intent": "extract"},
        operations=["extract"],
    )
    plan = build_plan_steps(classification, prompt="pull users table")
    assert_pipeline_steps(plan, ["extract"])


def test_matrix_continuation_map_migrate():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "destination_name": "Export",
            "active_job_id": "job-1",
            "_session_progress": {"mapping_ready": False},
            "plan_route": "continuation_path",
            "_continuation_steps": ["map", "migrate"],
        },
        operations=["map", "migrate"],
    )
    plan = build_plan_steps(classification, prompt="map and migrate")
    assert_pipeline_steps(plan, ["map", "migrate"])
    assert "extract" not in (plan.metadata.get("pipeline_steps") or [])


def test_matrix_recovery_map_migrate_no_source_picker():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "retry_after_partial": True,
        "active_job_id": "job-1",
        "_plan_scope": {"operations": ["map", "migrate"]},
    }
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["map", "migrate"],
    )
    reqs = read_plan_requirements(classification)
    assert SOURCE_ENTITY not in {s.slot for s in reqs.all_slots()}
    assert classification_needs_source_endpoint(classification) is False
    plan = build_plan_steps(classification, prompt="fix migration errors")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["map_fields", "start_migration"]
    clar = Clarification(
        id=str(uuid.uuid4()),
        clarification_type="pick_source_connector",
        prompt="Where to extract from?",
        options=[],
        required=True,
    )
    merged = {"execution_plan": plan.model_dump(), "clarifications": [clar.model_dump(mode="json")]}
    assert len(_clarification_from_plan(merged)) == 1
