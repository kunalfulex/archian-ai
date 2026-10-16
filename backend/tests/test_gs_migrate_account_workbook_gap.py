"""Phase 2: migrate to GS must emit pick_workbook after account pick (no empty_plan dead-end)."""
from __future__ import annotations

import uuid

from services.planning.clarification_apply import apply_clarification_selections
from services.planning.gap_detector import _build_pick_google_account_clarification
from services.planning.op_requirements import (
    append_requirement_clarifications_to_plan,
    read_plan_requirements,
    ready_to_build_plan,
)
from services.planning.plan_emit_recovery import try_recover_empty_plan_at_emit
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)
from services.planning.schemas import ExecutionPlan
from tests.plan_scope_helpers import with_plan_scope


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-a", name="Work Account"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="Lead Export",
                sheet_name="Sheet1",
                spreadsheet_id="sid-lead",
                google_sheets_connection_id="conn-a",
            ),
        ],
    )


def _migrate_classification(*, account_picked: bool = False) -> dict:
    cls = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "_source_endpoint_clarified": True,
            "planner_source_entity_confirmed": True,
        },
        operations=["extract", "map", "migrate"],
        needs_destination=True,
    )
    cls = sync_scope_into_classification(
        cls,
        cls["_plan_scope"],
        ["extract", "map", "migrate"],
    )
    if account_picked:
        cls["google_sheets_connection_id"] = "conn-a"
        cls["destination_connector_id"] = "conn-a"
        cls["destination_account_name"] = "Work Account"
        cls["_destination_connector_validated"] = True
        cls["_destination_endpoint_clarified"] = True
    return cls


def test_account_pick_emits_pick_workbook_for_migrate_export():
    cat = _catalog()
    cls = _migrate_classification(account_picked=True)
    cls["_planning_catalog"] = cat
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    assert not ready_to_build_plan(cls, parsed_goal=goal, requirements=reqs)

    plan = append_requirement_clarifications_to_plan(
        ExecutionPlan(),
        cls,
        reqs,
        parsed_goal=goal,
        catalog=cat,
    )
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_workbook" in open_types
    assert "create_workbook_name" not in open_types


def test_apply_account_selection_queues_workbook_not_dead_end():
    cat = _catalog()
    cls = _migrate_classification()
    cls["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets"},
        cat,
    )
    assert account_clar is not None
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    plan = append_requirement_clarifications_to_plan(
        plan,
        cls,
        reqs,
        parsed_goal=goal,
        catalog=cat,
    )
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_workbook" in open_types


def test_emit_recovery_returns_workbook_clarification():
    cat = _catalog()
    cls = _migrate_classification(account_picked=True)
    cls["_planning_catalog"] = cat
    plan = ExecutionPlan(plan_id=str(uuid.uuid4()), status="draft", steps=[])

    import asyncio

    out, ep, recovered = asyncio.run(
        try_recover_empty_plan_at_emit(
            cls,
            plan,
            prompt="export leads from SF 1 to google sheet",
            user_id="user-1",
        )
    )
    assert recovered is True
    blocking = ep.blocking_clarifications()
    assert blocking
    assert blocking[0].clarification_type == "pick_workbook"
    assert out.get("needs_clarification") is True
    assert out.get("plan_status") == "awaiting_clarification"
