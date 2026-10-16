"""File source treated as first-class connector in planning / binding / gaps."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.file_extraction.pending_upload import (
    is_file_source,
    pin_file_upload_on_classification,
    reconcile_plan_scope_with_file_source,
)
from services.intent.gaps import _needs_source_entity, detect_planner_gaps
from services.intent.state import IntentState
from services.planning.op_requirements import (
    assess_plan_requirements,
    read_plan_requirements,
    requirements_need_source_bind,
    ready_to_build_plan,
)
from services.planning.plan_extract_gates import ready_to_build_file_extract_plan
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import (
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)
from services.planning.schemas import merge_plan_into_classification, ExecutionPlan


def _pinned_file_classification(**extra):
    base = pin_file_upload_on_classification(
        {"intent": "extract", "operations": ["extract"], **extra},
        filename="Customer Contact Details.xlsx",
    )
    base["_pending_file_ref"] = {"file_key": "pending-upload/u/c/f.xlsx", "filename": base["source_name"]}
    return base


def test_is_file_source_pinned_and_explicit():
    cls = _pinned_file_classification()
    assert is_file_source(cls)
    assert cls.get("planner_source_entity_confirmed") is True


def test_requirements_skip_source_bind_for_file():
    cls = _pinned_file_classification()
    reqs = read_plan_requirements(cls)
    assert not requirements_need_source_bind(reqs, cls)
    gaps, _ = assess_plan_requirements(cls, reqs)
    assert not any(g.get("slot") == "source_entity" for g in gaps)


def test_ready_to_build_file_extract_plan():
    cls = _pinned_file_classification()
    assert ready_to_build_file_extract_plan(cls, {"operations": ["extract"]})


def test_needs_source_entity_gap_skipped_for_file():
    state = IntentState(prompt="extract data from the file")
    state.source_type = "file"
    state.operations = ["extract"]
    cls = _pinned_file_classification()
    assert not _needs_source_entity(state, cls)


def test_detect_gaps_no_pick_source_entity_for_file():
    import asyncio

    state = IntentState(prompt="extract data from the file")
    state.source_type = "file"
    state.operations = ["extract"]
    cls = _pinned_file_classification()
    cat = ResourceCatalog(
        sql_connections=[],
        salesforce_connections=[],
        google_sheet_rows=[],
        has_google_sheets=False,
    )

    async def _run():
        return await detect_planner_gaps(state, cls, prompt=state.prompt, catalog=cat)

    gaps, _clars = asyncio.run(_run())
    assert not any(g.get("clarification_type") == "pick_source_entity" for g in gaps)


def test_merge_plan_preserves_intent_needs_clarification():
    cls = {"needs_clarification": True, "clarifications": [{"clarification_type": "pick_destination_connector"}]}
    plan = ExecutionPlan(plan_id="p1", status="draft", steps=[])
    merged = merge_plan_into_classification(cls, plan)
    assert merged.get("needs_clarification") is True


def test_ready_to_build_plan_file_extract_only():
    cls = _pinned_file_classification()
    reqs = read_plan_requirements(cls)
    assert ready_to_build_plan(cls, requirements=reqs)


def test_reconcile_plan_scope_with_file_source():
    cls = _pinned_file_classification()
    goal = {
        "operations": ["extract", "map", "migrate", "create"],
        "needs_source": True,
        "source_phrase": "extract sales lead data from SF 1 org",
    }
    out, parsed = reconcile_plan_scope_with_file_source(cls, goal)
    assert parsed.get("needs_source") is False
    assert "source_phrase" not in parsed
    assert out.get("needs_source") is False


def test_sync_scope_into_classification_clears_needs_source_for_file():
    cls = _pinned_file_classification(destination="google_sheets")
    goal = {
        "operations": ["extract", "map", "migrate", "create"],
        "needs_source": True,
        "source_phrase": "extract sales lead data from SF 1 org",
        "needs_destination": True,
    }
    out = sync_scope_into_classification(cls, goal, ["extract", "map", "migrate", "create"])
    assert is_file_source(out)
    assert out.get("needs_source") is False
    assert (out.get("_plan_scope") or {}).get("needs_source") is False
    assert "source_phrase" not in (out.get("_plan_scope") or {})


def test_run_planning_kernel_preserves_file_source_after_destination_clar():
    cat = ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-1", name="Work Account"),
        ],
        google_sheet_rows=[],
        salesforce_connections=[SalesforceConnectionRef(name="SF 1", token_id="tok-1")],
    )
    cls = _pinned_file_classification(
        destination="google_sheets",
        destination_connector_id="conn-1",
        google_sheets_connection_id="conn-1",
        _destination_endpoint_clarified=True,
        intent="migrate_data",
        operations=["extract", "map", "migrate", "create"],
    )
    goal = {
        "operations": ["extract", "map", "migrate", "create"],
        "needs_source": True,
        "source_phrase": "extract sales lead data from SF 1 org",
        "needs_destination": True,
        "destination_phrase": "new Google Sheet",
    }
    cls, goal = reconcile_plan_scope_with_file_source(cls, goal)
    cls["_plan_scope"] = goal
    plan = ExecutionPlan(plan_id="p1", status="awaiting_clarification", steps=[])

    async def _run():
        from services.planning.clarification_pipeline import PlannerSession
        from services.planning.planning_kernel import run_planning_kernel

        session = PlannerSession(
            classification=cls,
            plan=plan,
            parsed_goal=goal,
            prompt="move the data to new google sheet, remove invalid records from the file",
        )
        with patch(
            "services.planning.source_llm_resolve.resolve_source_connector_via_llm",
            new_callable=AsyncMock,
        ) as mock_src_llm:
            mock_src_llm.side_effect = AssertionError(
                "source connector LLM must not run when file is pinned"
            )
            return await run_planning_kernel(
                session,
                plan_route="fresh_path",
                user_id="user-1",
                catalog=cat,
                run_source_resolver=True,
                run_destination_resolver=False,
            )

    session = asyncio.run(_run())
    assert is_file_source(session.classification)
    assert session.classification.get("source_name") == "Customer Contact Details.xlsx"
    clar_types = [
        (c.get("clarification_type") if isinstance(c, dict) else getattr(c, "clarification_type", ""))
        for c in (session.classification.get("clarifications") or [])
    ]
    assert "pick_source_connector" not in clar_types
