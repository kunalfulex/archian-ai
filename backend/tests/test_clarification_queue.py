"""Clarification queue — slot dedupe, prune, export flow."""
from __future__ import annotations

from services.planning.clarification_apply import apply_clarification_selections
from services.planning.clarification_queue import (
    build_ranked_slot_clarifications,
    build_requirement_clarifications,
    clarification_from_extract_fields_intent,
    is_metadata_clarification_resume,
    merge_plan_clarification_queue,
    prune_plan_clarifications,
)
from services.planning.gap_detector import _build_pick_google_account_clarification
from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.op_requirements import read_plan_requirements
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import GoogleSheetsConnectionRef, ResourceCatalog
from services.planning.schemas import Clarification, ExecutionPlan
from tests.plan_scope_helpers import with_plan_scope


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-a", name="Work Account"),
        ],
    )


def _export_classification() -> dict:
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "planner_create_spreadsheet": True,
            "_source_endpoint_clarified": True,
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
    )
    return sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "create", "map", "migrate"],
    )


def test_build_queue_no_headers_or_duplicate_connector_for_export():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    queue = build_requirement_clarifications(
        cls, reqs, parsed_goal=goal, catalog=cat
    )
    types = [c.clarification_type for c in queue]
    assert "pick_create_headers" not in types
    assert "pick_destination_connector" not in types
    assert "pick_google_account" in types
    assert "create_workbook_name" in types


def test_merge_queue_no_pick_destination_connector_after_google_account():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    assert account_clar is not None
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    plan = ExecutionPlan(
        steps=[],
        clarifications=[
            account_clar,
            Clarification(
                id="dest-generic",
                clarification_type="pick_destination_connector",
                prompt="Where?",
                options=[],
            ),
        ],
    )
    plan = merge_plan_clarification_queue(
        plan, cls, reqs, parsed_goal=goal, catalog=cat
    )
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_destination_connector" not in open_types
    assert "pick_google_account" in open_types


def test_prune_drops_satisfied_destination_connector_after_account_pick():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    plan = ExecutionPlan(
        steps=[],
        clarifications=[
            account_clar,
            Clarification(
                id="dest-generic",
                clarification_type="pick_destination_connector",
                prompt="Where?",
                options=[],
            ),
            Clarification(
                id="title",
                clarification_type="create_workbook_name",
                prompt="Name?",
                options=[],
            ),
        ],
    )
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
    )
    plan = prune_plan_clarifications(plan, cls, reqs, parsed_goal=goal)
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_destination_connector" not in open_types
    assert "pick_google_account" not in open_types
    assert "create_workbook_name" in open_types


def test_is_metadata_clarification_resume():
    assert is_metadata_clarification_resume(["pick_google_account", "create_workbook_name"])
    assert is_metadata_clarification_resume(["pick_destination_entity"])
    assert not is_metadata_clarification_resume(["pick_extract_fields"])


def _sf_lead_pipeline_intent() -> PromptMigrationIntent:
    return PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="Lead",
        driving_entity_label="Lead",
        extract_field_scope="vague",
        field_hints=["Name", "Company"],
        field_hints_strict=False,
        suggested_field_hints=["Name", "Company", "Email", "Phone"],
        needs_extract_clarification=True,
        extract_fields_confirmed=False,
        extract_ambiguities=[
            ExtractAmbiguity(
                ambiguity_type="pick_extract_fields",
                prompt="Which Lead fields should we extract from SF 1?",
            )
        ],
    )


def _sf_to_sf_lead_classification() -> dict:
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "_source_endpoint_clarified": True,
            "destination": "salesforce",
            "destination_name": "SF 2",
            "destination_object_api_name": "Lead",
            "planner_destination_entity_confirmed": True,
        },
        operations=["extract", "migrate"],
    )
    return sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "migrate"],
    )


def test_clarification_from_extract_fields_intent_builds_options():
    intent = _sf_lead_pipeline_intent()
    clar = clarification_from_extract_fields_intent(intent)
    assert clar is not None
    assert clar.clarification_type == "pick_extract_fields"
    labels = {o.label for o in clar.options}
    assert "Name" in labels
    assert "All descriptive columns (recommended)" in labels


def test_merge_queue_emits_pick_extract_fields_from_ranked_slot():
    cls = _sf_to_sf_lead_classification()
    goal = dict(cls["_plan_scope"])
    intent = _sf_lead_pipeline_intent()
    reqs = read_plan_requirements(cls, parsed_goal=goal)

    attached = Clarification(
        id="pipeline-field-clar",
        clarification_type="pick_extract_fields",
        prompt="Which Lead fields should we extract from SF 1?",
        options=[],
        required=True,
    )
    plan = ExecutionPlan(steps=[], clarifications=[attached])

    merged = merge_plan_clarification_queue(
        plan,
        cls,
        reqs,
        parsed_goal=goal,
        migration_intent=intent,
    )
    open_types = [
        c.clarification_type
        for c in merged.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_extract_fields" in open_types
    field_clar = next(
        c for c in merged.clarifications if c.clarification_type == "pick_extract_fields"
    )
    assert field_clar.options


def test_build_ranked_slot_clarifications_skips_when_confirmed():
    cls = _sf_to_sf_lead_classification()
    goal = dict(cls["_plan_scope"])
    intent = _sf_lead_pipeline_intent().model_copy(update={"extract_fields_confirmed": True})
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    ranked = build_ranked_slot_clarifications(
        cls, reqs, parsed_goal=goal, migration_intent=intent
    )
    assert ranked == []
