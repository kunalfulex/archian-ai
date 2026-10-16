"""GS destination entity: async merge must emit pick_workbook, not tab fallback."""
from __future__ import annotations

import asyncio

from services.planning.clarification_queue import merge_plan_clarification_queue_async
from services.planning.op_requirements import read_plan_requirements
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)
from services.planning.schemas import ExecutionPlan
from tests.plan_scope_helpers import with_plan_scope

_CONN_ID = "3155a477-d9fd-470b-a3a8-a623af01a8d9"


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id=_CONN_ID, name="Archian AI"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="Lead Export",
                sheet_name="Sheet1",
                spreadsheet_id="sid-lead",
                google_sheets_connection_id=_CONN_ID,
            ),
        ],
    )


def _continuation_gs_account_picked() -> dict:
    cls = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "source_entity_key": "Lead",
            "destination": "google_sheets",
            "active_job_id": "18403c73-c753-4807-a38c-c2aa30646400",
            "planner_source_entity_confirmed": True,
            "google_sheets_connection_id": _CONN_ID,
            "destination_connector_id": _CONN_ID,
            "destination_account_name": "Archian AI",
            "_destination_connector_validated": True,
            "_destination_endpoint_clarified": True,
            "plan_contract": {
                "source": {
                    "connector_type": "salesforce",
                    "display_name": "SF 1",
                    "entity_key": "Lead",
                    "entity_label": "Lead",
                    "catalog_label": "Lead",
                    "resolution_source": "plan_pinned",
                    "confidence": 0.95,
                },
                "destination": {
                    "connector_type": "google_sheets",
                    "connector_id": _CONN_ID,
                    "display_name": "Archian AI",
                    "resolution_source": "llm_bind",
                    "confidence": 0.9,
                },
            },
        },
        operations=["map", "migrate"],
        needs_destination=True,
    )
    goal = dict(cls["_plan_scope"])
    return sync_scope_into_classification(cls, goal, ["map", "migrate"])


async def _merge_async(cls: dict, *, catalog: ResourceCatalog) -> ExecutionPlan:
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    return await merge_plan_clarification_queue_async(
        ExecutionPlan(),
        cls,
        reqs,
        user_id="user-test",
        parsed_goal=goal,
        catalog=catalog,
        prompt="can you migrate the extracted data to google sheet",
    )


def test_async_merge_emits_pick_workbook_after_account_pick():
    """Production path: merge_plan_clarification_queue_async + entity_resolver."""
    cat = _catalog()
    cls = _continuation_gs_account_picked()
    cls["_planning_catalog"] = cat

    plan = asyncio.run(_merge_async(cls, catalog=cat))

    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_workbook" in open_types
    assert "pick_destination_entity" not in open_types
    assert "create_workbook_name" not in open_types


def test_async_merge_tab_picker_after_workbook_pinned():
    """After workbook resolve, entity_resolver may rank tabs (not workbook picker)."""
    cat = _catalog()
    cls = _continuation_gs_account_picked()
    cls["destination_name"] = "Lead Export"
    cls["destination_spreadsheet_id"] = "sid-lead"
    cls["_planning_catalog"] = cat
    contract = dict(cls["plan_contract"])
    contract["destination"] = {
        **contract["destination"],
        "display_name": "Lead Export",
        "entity_key": "sid-lead::Sheet1",
        "spreadsheet_id": "sid-lead",
        "sheet_name": "Sheet1",
        "resolution_source": "catalog",
        "confidence": 0.9,
    }
    cls["plan_contract"] = contract

    plan = asyncio.run(_merge_async(cls, catalog=cat))

    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_workbook" not in open_types
