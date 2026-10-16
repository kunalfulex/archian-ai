"""Tests for intent pipeline Phase 1–2."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from services.intent.flags import intent_pipeline_v2_enabled
from services.intent.pipeline import run_intent_pipeline
from services.intent.project import merge_intent_into_classification, project_to_classification
from services.intent.state import ConnectorRef, IntentState
from services.intent.steps.destination_need import derive_destination_need
from services.intent.types import DestinationNeed, IntentStepId, TurnType
from services.planning.resource_catalog import ResourceCatalog, SqlConnectionRef


FIXTURES_PATH = Path(__file__).parent / "fixtures" / "intent_golden_prompts.json"


def test_intent_pipeline_always_enabled():
    assert intent_pipeline_v2_enabled() is True


def test_s04_extract_only_no_destination():
    state = IntentState(
        prompt="extract user data from Test DB",
        turn_type=TurnType.EXTRACT.value,
        operations=["extract"],
        classifier_snapshot={
            "intent": "extract",
            "source": "sql",
            "destination": "salesforce",
            "_pipeline_tokens": ["extract"],
        },
    )
    assert derive_destination_need(state) == DestinationNeed.NONE.value


def test_s04_extract_to_sheets_needs_destination():
    state = IntentState(
        prompt="extract contacts and create new sheet",
        turn_type=TurnType.EXTRACT.value,
        operations=["extract"],
        classifier_snapshot={
            "intent": "extract",
            "source": "salesforce",
            "destination": "google_sheets",
            "planner_destination_confirmed": True,
            "planner_create_spreadsheet": True,
        },
    )
    assert derive_destination_need(state) == DestinationNeed.REQUIRED.value


def test_merge_clears_destination_for_extract_only():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "destination_name": "SF Prod",
        "intent": "extract",
        "operations": ["extract"],
    }
    state = IntentState(
        prompt="extract user data from Test DB",
        turn_type=TurnType.EXTRACT.value,
        operations=["extract"],
        needs_destination=DestinationNeed.NONE.value,
        source_type="sql",
        source=ConnectorRef(connector_type="sql", display_name="Test DB", resolved=True),
        steps_completed=[IntentStepId.S04_DESTINATION_NEED.value],
    )
    merged = merge_intent_into_classification(cls, state)
    assert merged["destination"] == "unknown"
    assert merged.get("destination_name") is None


def test_pre_planning_pipeline_with_catalog():
    catalog = ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "destination": "salesforce",
        "intent": "extract",
        "mode": "extract",
        "operations": ["extract"],
        "_pipeline_tokens": ["extract"],
        "reasoning": "Extract from SQL",
    }

    async def _run():
        return await run_intent_pipeline(
            "extract user data from Test DB",
            user_id="user-1",
            classification=cls,
            catalog=catalog,
            phase="pre_planning",
        )

    result = asyncio.run(_run())
    assert IntentStepId.S04_DESTINATION_NEED in result.steps_completed
    assert result.state.needs_destination == DestinationNeed.NONE.value
    assert result.state.source.display_name == "Test DB"


def test_post_planning_entity_from_migration_intent():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "intent": "extract",
        "migration_intent": {
            "driving_entity_key": "users",
            "driving_entity_label": "users",
            "extract_field_scope": "explicit",
            "field_hints": ["first name", "last name", "email"],
            "field_hints_strict": True,
            "extract_fields_confirmed": True,
        },
    }

    async def _run():
        return await run_intent_pipeline(
            "extract first name, last name and email from user table from Test DB database",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert IntentStepId.S07_ENTITY in result.steps_completed
    assert IntentStepId.S08_FIELD_SCOPE in result.steps_completed
    assert result.state.source_entity.entity_key == "users"
    assert result.state.fields.confirmed is True


def test_project_to_classification_destination_unknown():
    state = IntentState(
        turn_type=TurnType.EXTRACT.value,
        needs_destination=DestinationNeed.NONE.value,
        source_type="sql",
        source=ConnectorRef(connector_type="sql", display_name="Test DB"),
        steps_completed=[IntentStepId.S04_DESTINATION_NEED.value],
    )
    out = project_to_classification(state)
    assert out["destination"] == "unknown"


def test_golden_fixtures_file_loads():
    assert FIXTURES_PATH.is_file()
    rows = json.loads(FIXTURES_PATH.read_text())
    assert len(rows) >= 12


def test_explicit_fields_post_pipeline_no_field_gap():
    """P2.6: explicit migration_intent → sql/users/explicit, no pick_extract_fields gap."""
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_table_name": "users",
        "intent": "extract",
        "operations": ["extract"],
        "migration_intent": {
            "driving_entity_key": "users",
            "driving_entity_label": "users",
            "extract_field_scope": "explicit",
            "field_scope_confidence": 0.95,
            "field_hints": ["first name", "last name", "email"],
            "field_hints_strict": True,
            "extract_fields_confirmed": True,
        },
    }

    async def _run():
        return await run_intent_pipeline(
            "extract first name, last name and email from user table from Test DB database",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert IntentStepId.S09_OPERATIONS in result.steps_completed
    assert IntentStepId.S11_PLAN_SKETCH in result.steps_completed
    assert result.state.fields.confirmed is True
    assert result.state.active_gap is None
    pick_field_gaps = [
        g for g in result.state.gaps if g.get("clarification_type") == "pick_extract_fields"
    ]
    assert not pick_field_gaps


def test_post_pipeline_operations_from_classification():
    cls = {
        "source": "sql",
        "intent": "extract",
        "operations": ["extract", "transform"],
        "_plan_scope": {"operations": ["extract"]},
    }

    async def _run():
        return await run_intent_pipeline(
            "extract users from Test DB",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert "extract" in result.state.operations


def test_merge_sets_endpoints_resolved_when_source_bound():
    cls = {"source": "unknown", "destination": "salesforce", "intent": "extract"}
    state = IntentState(
        prompt="extract from Test DB",
        turn_type=TurnType.EXTRACT.value,
        source=ConnectorRef(connector_type="sql", display_name="Test DB", resolved=True),
    )
    merged = merge_intent_into_classification(cls, state)
    assert merged.get("_endpoints_resolved") is True


def test_resume_step_mapping():
    from services.intent.clarification_types import ALL_CLARIFICATION_TYPES
    from services.intent.resume import (
        resume_phase_for_clarification,
        resume_step_after_clarification,
    )
    from services.intent.types import IntentStepId

    assert resume_phase_for_clarification("pick_workbook") == "pre_planning"
    assert resume_step_after_clarification("pick_workbook") == IntentStepId.S06_BIND_RESOURCES
    assert resume_phase_for_clarification("pick_extract_fields") == "post_planning"
    assert resume_step_after_clarification("pick_extract_fields") == IntentStepId.S08_FIELD_SCOPE
    assert "pick_extract_fields" in ALL_CLARIFICATION_TYPES


def test_resolve_next_gap_clarification_from_plan():
    from services.intent.gaps import resolve_next_gap_clarification
    from services.intent.state import IntentState

    state = IntentState(prompt="extract users")
    cls = {
        "needs_clarification": True,
        "clarifications": [
            {
                "id": "c1",
                "clarification_type": "pick_extract_fields",
                "prompt": "Which columns?",
                "options": [],
                "required": True,
            }
        ],
    }

    async def _run():
        return await resolve_next_gap_clarification(
            state, cls, prompt="extract users", catalog=None
        )

    clar = asyncio.run(_run())
    assert clar is not None
    assert clar.clarification_type == "pick_extract_fields"
    assert state.active_gap == "pick_extract_fields"


def test_pipeline_start_after_step_skips_early_post_steps():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "intent": "extract",
        "migration_intent": {
            "driving_entity_key": "users",
            "extract_field_scope": "explicit",
            "field_hints": ["email"],
            "field_hints_strict": True,
            "extract_fields_confirmed": True,
        },
    }

    async def _run():
        return await run_intent_pipeline(
            "extract email from users in Test DB",
            classification=cls,
            phase="post_planning",
            start_after_step=IntentStepId.S08_FIELD_SCOPE,
        )

    result = asyncio.run(_run())
    assert IntentStepId.S07_ENTITY not in result.steps_completed
    assert IntentStepId.S08_FIELD_SCOPE in result.steps_completed
    assert IntentStepId.S12_READY in result.steps_completed


def test_pre_planning_marks_s02_and_s03_together():
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "intent": "extract",
        "goal_summary": "Extract users from Test DB",
    }

    async def _run():
        return await run_intent_pipeline(
            "extract users from Test DB",
            classification=cls,
            phase="pre_planning",
        )

    result = asyncio.run(_run())
    assert IntentStepId.S02_TURN_TYPE in result.steps_completed
    assert IntentStepId.S03_GOAL in result.steps_completed


def test_s07_catalog_resolution_overrides_low_confidence_heuristic(monkeypatch):
    from services.planning.schemas import PlanEntityRef

    async def _fake_resolve(**kwargs):
        assert kwargs.get("role") == "source"
        return PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            entity_label="Users",
            confidence=0.95,
            resolution_source="catalog_label",
        )

    monkeypatch.setattr(
        "services.planning.entity_resolver._resolve_side_entity",
        _fake_resolve,
    )

    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "conn-1",
        "intent": "extract",
    }

    async def _run():
        return await run_intent_pipeline(
            "extract from user table in Test DB",
            user_id="user-1",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert result.state.source_entity.entity_key == "users"
    assert result.state.source_entity.confidence >= 0.95
