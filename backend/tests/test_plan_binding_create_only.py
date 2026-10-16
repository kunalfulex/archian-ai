"""Integration: create-only binding path must not block plan assembly."""
from __future__ import annotations

import asyncio
import logging
import uuid

from services.intent.plan_assembly import assemble_migration_plan_tail
from services.planning.binding.pipeline import run_plan_binding_pipeline
from services.planning.create_metadata import apply_create_metadata_to_classification
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan


def _create_only_classification():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Archian Data",
        "spreadsheet_headers": ["Name", "Email", "Cellphone number", "Address"],
    }
    classification = apply_create_metadata_to_classification(
        {
            "destination": "google_sheets",
            "intent": "create",
            "mode": "create",
            "operations": ["create"],
            "plan_route": "fresh_path",
            "planner_spreadsheet_title_confirmed": True,
        },
        goal,
    )
    classification["planner_new_spreadsheet_name"] = goal["spreadsheet_title"]
    classification["_destination_endpoint_clarified"] = True
    return sync_scope_into_classification(classification, goal, ["create"]), goal


def test_binding_create_only_no_source_clarification():
    classification, goal = _create_only_classification()
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        metadata={"classification": dict(classification)},
    )
    catalog = ResourceCatalog()

    out, plan, migration_intent, contract = asyncio.run(
        run_plan_binding_pipeline(
            classification,
            plan,
            user_id="test-user",
            prompt="create new sheet for Archian Data with header Name, Email, Cellphone number and Address",
            parsed_goal=goal,
            catalog=catalog,
        )
    )

    clar_types = [c.clarification_type for c in plan.clarifications]
    assert "pick_source_entity" not in clar_types
    assert not plan.blocking_clarifications()


def test_assemble_create_only_builds_plan_step():
    classification, goal = _create_only_classification()
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        metadata={"classification": dict(classification)},
    )
    catalog = ResourceCatalog()

    out, plan, _, contract = asyncio.run(
        run_plan_binding_pipeline(
            classification,
            plan,
            user_id="test-user",
            prompt="create new sheet for Archian Data with header Name, Email, Cellphone number and Address",
            parsed_goal=goal,
            catalog=catalog,
        )
    )

    out, plan, _, _ = asyncio.run(
        assemble_migration_plan_tail(
            out,
            plan,
            user_id="test-user",
            prompt="create new sheet for Archian Data",
            parsed_goal=goal,
            catalog=catalog,
            migration_intent=None,
        )
    )

    assert len(plan.steps) == 1
    assert plan.steps[0].parameters.get("tool_id") == "google_sheets_create_from_prompt"
    assert plan.metadata.get("pipeline_steps") == ["create"]


def test_create_only_binding_logs_skip_source(caplog):
    classification, goal = _create_only_classification()
    plan = ExecutionPlan(
        plan_id=str(uuid.uuid4()),
        status="draft",
        metadata={"classification": dict(classification)},
    )
    catalog = ResourceCatalog()

    with caplog.at_level(logging.INFO, logger="edison.plan_bind"):
        asyncio.run(
            run_plan_binding_pipeline(
                classification,
                plan,
                user_id="test-user",
                prompt="create new sheet for Archian Data",
                parsed_goal=goal,
                catalog=catalog,
            )
        )

    combined = caplog.text.lower()
    assert "pick_source_entity" not in combined
    assert "not required" in combined or "source_skipped" in combined
