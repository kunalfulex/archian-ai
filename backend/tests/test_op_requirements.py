"""Tests for declarative operation requirements (op_requirements.py)."""
from __future__ import annotations

from services.planning.create_metadata import apply_create_metadata_to_classification
from services.planning.op_requirements import (
    CREATE_HEADERS,
    SOURCE_ENTITY,
    assess_plan_requirements,
    derive_plan_requirements,
    ready_to_build_plan,
    requirements_need_source_bind,
)
from services.planning.pipeline_step_resolver import PlanContext
from services.planning.plan_scope import sync_scope_into_classification
from tests.plan_scope_helpers import with_plan_scope


def test_create_only_has_no_source_entity_slot():
    reqs = derive_plan_requirements(
        ["create"],
        classification={"destination": "google_sheets", "intent": "create"},
        parsed_goal={"operations": ["create"], "create_destination": True},
    )
    slots = {s.slot for s in reqs.all_slots()}
    assert SOURCE_ENTITY not in slots
    assert not requirements_need_source_bind(reqs)


def test_extract_requires_source_slots():
    reqs = derive_plan_requirements(
        ["extract"],
        classification={"source": "sql", "intent": "extract"},
        parsed_goal={"operations": ["extract"]},
    )
    slots = {s.slot for s in reqs.all_slots()}
    assert SOURCE_ENTITY in slots
    assert requirements_need_source_bind(reqs)


def test_create_only_assess_satisfied_with_metadata():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Archian Data",
        "spreadsheet_headers": ["Name", "Email", "Cellphone number", "Address"],
    }
    classification = apply_create_metadata_to_classification(
        {
            "destination": "google_sheets",
            "destination_connector_id": "gs-acc-1",
            "planner_destination_confirmed": True,
            "_destination_connector_validated": True,
            "intent": "create",
            "mode": "create",
            "operations": ["create"],
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Archian Data",
        },
        goal,
    )
    classification = sync_scope_into_classification(classification, goal, ["create"])
    reqs = derive_plan_requirements(
        ["create"],
        classification=classification,
        parsed_goal=goal,
    )
    gaps, clars = assess_plan_requirements(classification, reqs, parsed_goal=goal)
    assert gaps == []
    assert clars == []
    assert ready_to_build_plan(classification, parsed_goal=goal, requirements=reqs)


def test_create_only_missing_headers_gap():
    classification = with_plan_scope(
        {
            "destination": "google_sheets",
            "intent": "create",
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "Archian Data",
        },
        operations=["create"],
        create_destination=True,
    )
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["create"],
    )
    reqs = derive_plan_requirements(["create"], classification=classification)
    gaps, _ = assess_plan_requirements(classification, reqs)
    assert any(g["slot"] == CREATE_HEADERS for g in gaps)


def test_upsert_requires_target_entity_not_source():
    classification = with_plan_scope(
        {
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
    reqs = derive_plan_requirements(["upsert"], classification=classification)
    slots = {s.slot for s in reqs.all_slots()}
    assert SOURCE_ENTITY not in slots
    from services.planning.op_requirements import TARGET_ENTITY

    assert TARGET_ENTITY in slots


def test_extract_create_union_includes_source_and_create_slots():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "intent": "extract",
        },
        operations=["extract", "create"],
        create_destination=True,
        spreadsheet_title="CRM",
        spreadsheet_header_source="from_extraction",
    )
    reqs = derive_plan_requirements(
        ["extract", "create"],
        classification=classification,
        parsed_goal=classification["_plan_scope"],
        plan_context=PlanContext.from_classification(classification),
    )
    slots = {s.slot for s in reqs.all_slots()}
    assert SOURCE_ENTITY in slots
    assert CREATE_HEADERS not in slots  # post-extract create uses extraction headers


def test_append_requirement_clarifications_attaches_create_workbook_name():
    from services.planning.op_requirements import append_requirement_clarifications_to_plan
    from services.planning.schemas import ExecutionPlan

    goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
    }
    classification = apply_create_metadata_to_classification(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_connector_id": "tok-1",
            "source_object_api_name": "Contact",
            "planner_source_entity_confirmed": True,
            "destination": "google_sheets",
            "destination_connector_id": "acc-1",
            "destination_name": "Archian Data",
            "planner_destination_confirmed": True,
            "_destination_connector_validated": True,
            "intent": "migrate_data",
            "planner_create_spreadsheet": True,
            "planner_workbook_picker_done": True,
            "extract_fields_confirmed": True,
        },
        goal,
    )
    reqs = derive_plan_requirements(
        ["extract", "create", "map", "migrate"],
        classification=classification,
        parsed_goal=goal,
    )
    plan = append_requirement_clarifications_to_plan(
        ExecutionPlan(),
        classification,
        reqs,
        parsed_goal=goal,
    )
    types = [c.clarification_type for c in plan.clarifications]
    assert "create_workbook_name" in types
    assert plan.status == "awaiting_clarification"
    assert plan.clarifications[0].clarification_type == "create_workbook_name"
