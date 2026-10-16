"""Tests for extract/source gating before destination entity and plan build."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.entity_resolver import build_entity_clarifications
from services.intent.plan_assembly import assemble_migration_plan_tail
from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.plan_extract_gates import (
    extract_fields_locked,
    needs_source_entity_clarification,
    ready_for_destination_entity_step,
    ready_for_extract_field_step,
    ready_to_build_migration_plan,
    source_extract_entity_resolved,
)
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, PlanContract, PlanEntityRef


def test_source_unresolved_blocks_destination_entity_step():
    contract = PlanContract(
        source=PlanEntityRef(connector_type="sql", display_name="Test DB", confidence=0.0),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            confidence=0.9,
        ),
    )
    intent = PromptMigrationIntent(cross_system=True, extract_fields_confirmed=False)
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
    }
    assert not source_extract_entity_resolved(contract)
    assert not ready_for_destination_entity_step(cls, contract, intent, cls["intent"], None)


def test_needs_source_entity_clarification_when_table_unpinned():
    contract = PlanContract(
        source=PlanEntityRef(connector_type="sql", display_name="Test DB", confidence=0.0),
        destination=PlanEntityRef(connector_type="salesforce", display_name="SF 1"),
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "intent": "migrate_data",
        "operations": ["extract", "migrate"],
    }
    goal = {"operations": ["extract", "migrate"]}
    assert needs_source_entity_clarification(cls, contract, goal)
    assert not ready_for_destination_entity_step(
        cls,
        contract,
        PromptMigrationIntent(cross_system=True),
        "deploy user data",
        goal,
    )


def test_needs_source_entity_clarification_waits_for_source_connector_name():
    contract = PlanContract(
        source=PlanEntityRef(connector_type="sql", display_name=None, confidence=0.0),
        destination=PlanEntityRef(connector_type="salesforce", display_name="SF 1"),
    )
    cls = {
        "source": "sql",
        "source_name": None,
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "operations": ["extract", "migrate"],
    }
    goal = {"operations": ["extract", "migrate"]}
    assert not needs_source_entity_clarification(cls, contract, goal)


def test_blocking_source_entity_clarification_when_no_catalog_options():
    contract = PlanContract(
        source=PlanEntityRef(connector_type="sql", display_name="Test DB", confidence=0.0),
        destination=PlanEntityRef(connector_type="salesforce", display_name="SF 1"),
    )
    plan = ExecutionPlan(plan_id="p1", status="draft")
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "operations": ["extract", "migrate"],
    }

    with patch(
        "services.mapping_plan.catalog_target_resolver._load_sql_connector_tables",
        new=AsyncMock(return_value=[]),
    ), patch(
        "utils.destination_connector_resolve.resolve_sql_connector_record",
        new=AsyncMock(return_value={"id": "sql-1"}),
    ), patch(
        "services.planning.entity_resolver.fetch_semantics_map",
        new=AsyncMock(return_value={}),
    ):
        clars = asyncio.run(
            build_entity_clarifications(
                contract,
                cls,
                user_id="user-1",
                prompt="deploy user data to salesforce",
                parsed_goal={"operations": ["extract", "migrate"]},
                roles=("source",),
            )
        )

    types = [c.clarification_type for c in clars]
    assert "pick_source_entity" in types


def test_extract_fields_locked_from_classification():
    intent = PromptMigrationIntent(
        cross_system=True,
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    cls = {"extract_fields_confirmed": True}
    assert extract_fields_locked(cls, intent)


def test_assemble_tail_builds_when_extract_fields_confirmed_despite_stale_intent():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            confidence=0.95,
            resolution_source="plan_pinned",
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            entity_key="Contact",
            confidence=0.95,
            resolution_source="plan_pinned",
        ),
    )
    stale_intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        needs_extract_clarification=True,
        extract_ambiguities=[
            ExtractAmbiguity(ambiguity_type="pick_extract_fields", prompt="Which columns?")
        ],
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "sql-1",
        "source_table_name": "users",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_connector_id": "sf-1",
        "destination_object_api_name": "Contact",
        "operations": ["extract", "map", "migrate"],
        "extract_fields_confirmed": True,
        "planner_destination_entity_confirmed": True,
        "_source_endpoint_clarified": True,
        "_destination_endpoint_clarified": True,
    }
    plan = ExecutionPlan(plan_id="p1")

    with patch(
        "services.planning.entity_resolver.resolve_plan_entities",
        new=AsyncMock(return_value=contract),
    ), patch(
        "services.planning.clarification_queue.build_entity_slot_clarifications",
        new=AsyncMock(side_effect=lambda *a, **k: []),
    ), patch(
        "services.planning.extract_field_clarification.resolve_field_clarification_for_plan",
        new=AsyncMock(return_value=stale_intent),
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        new=AsyncMock(side_effect=lambda out, **k: out),
    ), patch(
        "services.planning.destination_object_enrichment.should_defer_destination_object_enrichment_early",
        return_value=False,
    ):
        out, updated_plan, _, _ = asyncio.run(
            assemble_migration_plan_tail(
                cls,
                plan,
                user_id="user-1",
                prompt="deploy user data to salesforce",
                parsed_goal={"operations": ["extract", "map", "migrate"]},
                catalog=ResourceCatalog(),
                migration_intent=stale_intent,
            )
        )

    assert len(updated_plan.steps or []) > 0
    assert out.get("destination_object_api_name") == "Contact"


def test_ready_to_build_accepts_pinned_destination_without_contract_confidence():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="users",
            resolution_source="user_explicit",
            confidence=0.9,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="ExternalDataUserAuth",
            resolution_source="pending",
            confidence=0.4,
        ),
    )
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        extract_fields_confirmed=True,
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_table_name": "users",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_connector_id": "sf-1",
        "destination_object_api_name": "Contact",
        "intent": "migrate_data",
        "operations": ["extract", "map", "migrate"],
        "planner_destination_entity_confirmed": True,
        "_source_endpoint_clarified": True,
        "_destination_endpoint_clarified": True,
        "extract_fields_confirmed": True,
    }
    goal = {"operations": ["extract", "map", "migrate"]}
    assert ready_to_build_migration_plan(cls, contract, intent, "deploy user data", goal)


def test_ready_to_build_extract_only_without_destination():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="users",
            resolution_source="user_explicit",
            confidence=0.9,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name=None,
            confidence=0.0,
        ),
    )
    intent = PromptMigrationIntent(
        cross_system=False,
        driving_entity_key="users",
        field_hints=["first name", "last name", "email"],
        field_hints_strict=True,
        extract_field_scope="explicit",
        field_scope_confidence=0.9,
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_table_name": "users",
        "destination": "salesforce",
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract"],
        "_source_endpoint_clarified": True,
    }
    prompt = "extract first name, last name and email from user table from Test DB database"
    goal = {"operations": ["extract"]}
    assert ready_to_build_migration_plan(cls, contract, intent, prompt, goal)


def test_ready_to_build_requires_destination_entity_confirmed():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="users",
            resolution_source="user_explicit",
            confidence=0.9,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            resolution_source="pending",
            confidence=0.0,
        ),
    )
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        extract_fields_confirmed=True,
        field_hints=["email"],
        field_hints_strict=True,
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "intent": "migrate_data",
        "operations": ["extract", "migrate"],
    }
    goal = {"operations": ["extract", "migrate"]}
    assert not ready_to_build_migration_plan(cls, contract, intent, "deploy user data", goal)
    cls["planner_destination_entity_confirmed"] = True
    cls["destination_object_api_name"] = "Contact"
    cls["_destination_endpoint_clarified"] = True
    cls["destination_connector_id"] = "sf-1"
    contract = PlanContract(
        source=contract.source,
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            resolution_source="user_explicit",
            confidence=0.9,
        ),
    )
    assert ready_to_build_migration_plan(cls, contract, intent, "deploy user data", goal)


def test_active_job_map_continuation_ready_to_build_without_extract_gates():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            display_name="Test DB",
            entity_key="users",
            confidence=0.95,
            resolution_source="plan_pinned",
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            confidence=0.92,
            resolution_source="goal_entity",
        ),
    )
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        extract_fields_confirmed=True,
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_table_name": "users",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_object_api_name": "Contact",
        "destination_connector_id": "sf-1",
        "_destination_endpoint_clarified": True,
        "active_job_id": "job-abc",
        "intent": "map_data",
        "operations": ["map"],
    }
    goal = {"operations": ["map"]}
    assert ready_for_extract_field_step(cls, contract, goal)
    assert ready_to_build_migration_plan(
        cls,
        contract,
        intent,
        "map the data to SF 1 Contact object",
        goal,
    )
