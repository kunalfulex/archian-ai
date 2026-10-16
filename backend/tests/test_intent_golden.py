"""Golden intent pipeline tests (P5.1) — prompt fixtures → IntentState expectations."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from services.intent.pipeline import run_intent_pipeline
from services.intent.project import project_to_classification
from services.intent.state import IntentState
from services.intent.types import DestinationNeed, IntentStepId, TurnType
from services.planning.resource_catalog import ResourceCatalog, SqlConnectionRef

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "intent_golden_prompts.json"


def _load_golden_cases() -> List[Dict[str, Any]]:
    rows = json.loads(FIXTURES_PATH.read_text())
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def _test_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
    )


def _classification_for_case(case: Dict[str, Any]) -> Dict[str, Any]:
    case_id = case["id"]
    prompt = case.get("prompt") or ""

    if case_id == "extract_only_sql":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "destination": "salesforce",
            "intent": "extract",
            "operations": ["extract"],
            "_pipeline_tokens": ["extract"],
        }
    if case_id == "extract_explicit_fields_sql":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "intent": "extract",
            "operations": ["extract"],
            "migration_intent": {
                "driving_entity_key": "users",
                "driving_entity_label": "users",
                "extract_field_scope": "explicit",
                "field_hints": ["first name", "last name", "email"],
                "field_hints_strict": True,
                "extract_fields_confirmed": True,
            },
        }
    if case_id == "migrate_sql_to_sf":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "destination": "salesforce",
            "destination_name": "SF Prod",
            "destination_object_api_name": "Contact",
            "intent": "migrate_data",
            "operations": ["extract", "migrate"],
        }
    if case_id == "general_greeting":
        return {
            "source": "unknown",
            "destination": "unknown",
            "intent": "general",
            "conversational_turn": True,
        }
    if case_id == "vague_user_data":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "intent": "migrate_data",
            "operations": ["extract", "migrate"],
            "migration_intent": {
                "driving_entity_key": "users",
                "extract_field_scope": "vague",
                "field_scope_confidence": 0.6,
            },
        }
    if case_id == "continuation_map":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "destination": "salesforce",
            "destination_name": "SF Prod",
            "intent": "map_data",
            "operations": ["map"],
        }
    if case_id == "multi_source_extract":
        return {
            "source": "salesforce",
            "source_name": "SF Prod",
            "destination": "unknown",
            "intent": "extract",
            "multi_source_extract": True,
            "operations": ["extract"],
        }
    if case_id == "metadata_deploy_sf":
        return {
            "source": "salesforce",
            "source_name": "SF Prod",
            "destination": "salesforce",
            "destination_name": "SF Prod",
            "intent": "metadata_deploy",
            "operations": [],
        }
    if case_id == "gsheets_join":
        return {
            "source": "google_sheets",
            "source_name": "Employee list",
            "source_sheet_name": "Sheet1",
            "multi_source_extract": True,
            "extract_sources": [
                {"connector_type": "google_sheets", "display_name": "Employee list", "sheet_name": "Sheet1"},
                {"connector_type": "google_sheets", "display_name": "Employee list", "sheet_name": "Sheet2"},
            ],
            "join_key": "Email",
            "intent": "extract",
            "operations": ["extract", "join"],
        }
    if case_id == "test_db_users_table":
        return {
            "source": "sql",
            "source_name": "Test DB",
            "destination": "unknown",
            "intent": "extract",
            "operations": ["extract"],
            "migration_intent": {
                "driving_entity_key": "users",
                "driving_entity_label": "users",
            },
        }
    if case_id == "extract_create_sheet":
        return {
            "source": "salesforce",
            "source_name": "SF Prod",
            "destination": "google_sheets",
            "intent": "extract",
            "operations": ["extract"],
            "planner_destination_confirmed": True,
            "planner_create_spreadsheet": True,
        }
    if case_id == "sf_to_sf_migrate":
        return {
            "source": "salesforce",
            "source_name": "SF Prod",
            "destination": "salesforce",
            "destination_name": "SF Sandbox",
            "intent": "migrate_data",
            "operations": ["extract", "migrate"],
        }
    return {"intent": "extract", "source": "unknown", "destination": "unknown"}


def _phase_for_case(case: Dict[str, Any]) -> str:
    case_id = case["id"]
    if case_id in (
        "extract_explicit_fields_sql",
        "vague_user_data",
        "gsheets_join",
        "test_db_users_table",
    ):
        return "post_planning"
    return "pre_planning"


def _run_case(case: Dict[str, Any]):
    prompt = case.get("prompt") or ""
    cls = _classification_for_case(case)
    phase = _phase_for_case(case)
    catalog = _test_catalog() if phase == "pre_planning" else None

    intent_state_for_classifier = None
    if case.get("continuation"):
        intent_state_for_classifier = {
            "active_job_id": "job-golden-1",
            "job_id": "job-golden-1",
            "active_source": "sql",
            "source_name": "Test DB",
        }

    async def _run():
        return await run_intent_pipeline(
            prompt,
            user_id="golden-user",
            classification=cls,
            catalog=catalog,
            phase=phase,
            intent_state_for_classifier=intent_state_for_classifier,
        )

    return asyncio.run(_run())


def _assert_expectations(
    state: IntentState,
    expect: Dict[str, Any],
    *,
    phase: str,
) -> None:
    if "turn_type" in expect:
        assert state.turn_type == expect["turn_type"], state.turn_type
    if "needs_destination" in expect and phase == "pre_planning":
        assert state.needs_destination == expect["needs_destination"], state.needs_destination
    if "destination_type" in expect:
        dest = state.destination_type or state.destination.connector_type or "unknown"
        assert dest == expect["destination_type"], dest
    if "field_scope" in expect:
        assert state.fields.scope == expect["field_scope"], state.fields.scope
    if "source_entity_key" in expect:
        assert state.source_entity.entity_key == expect["source_entity_key"], state.source_entity.entity_key
    if "operations_include" in expect:
        assert expect["operations_include"] in (state.operations or []), state.operations


@pytest.mark.parametrize("case", _load_golden_cases(), ids=lambda c: c["id"])
def test_intent_golden_pipeline(case: Dict[str, Any]):
    result = _run_case(case)
    expect = case.get("expect") or {}
    _assert_expectations(result.state, expect, phase=_phase_for_case(case))
    projected = project_to_classification(result.state)
    if expect.get("turn_type") == "map":
        assert projected.get("intent") == "map_data"
    elif expect.get("turn_type") == "migrate":
        assert projected.get("intent") == "migrate_data"
    elif expect.get("turn_type") == "migrate":
        assert projected.get("intent") in ("migrate", "migrate_data")
    elif expect.get("turn_type") == "data_ops":
        assert projected.get("intent") in ("multi", "data_ops")
    elif expect.get("turn_type"):
        assert projected.get("intent") == expect["turn_type"] or projected.get("mode") == expect["turn_type"]


def test_s02_multi_source_extract_flag():
    state = IntentState(
        prompt="extract from SF and SQL",
        classifier_snapshot={"intent": "extract", "multi_source_extract": True},
    )

    async def _run():
        from services.intent.steps.turn_type import run_step_turn_type

        return await run_step_turn_type(state)

    asyncio.run(_run())
    assert state.turn_type == TurnType.DATA_OPS.value


def test_s09_metadata_deploy_operation():
    cls = {
        "source": "salesforce",
        "source_name": "SF Prod",
        "intent": "metadata_deploy",
        "operations": [],
    }

    async def _run():
        return await run_intent_pipeline(
            "deploy custom fields to production",
            classification=cls,
            phase="post_planning",
        )

    result = asyncio.run(_run())
    assert "deploy_schema" in result.state.operations


def test_pipeline_emits_step_metrics():
    catalog = _test_catalog()
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "intent": "extract",
        "operations": ["extract"],
    }

    async def _run():
        return await run_intent_pipeline(
            "extract user data from Test DB",
            user_id="u1",
            classification=cls,
            catalog=catalog,
            phase="pre_planning",
        )

    result = asyncio.run(_run())
    assert result.metrics is not None
    summary = result.metrics.summary()
    assert summary["phase"] == "pre_planning"
    assert summary["total_duration_ms"] >= 0
    assert IntentStepId.S04_DESTINATION_NEED.value in summary["steps_completed"]
