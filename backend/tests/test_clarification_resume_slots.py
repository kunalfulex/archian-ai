"""Slot-based clarification resume — continuation workbook pick must assemble plan."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.job_context import JobContext, ResourcePin
from services.planning.continuation_destination import enrich_continuation_destination_connectors
from services.planning.continuation_policy import (
    RESUME_MODE_CLARIFICATION,
    RESUME_MODE_SCOPE_TURN,
    apply_continuation_endpoint_pins_from_job,
    continuation_needs_destination_llm_resolution,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)
from services.planning.schemas import ExecutionPlan
from services.planning.slot_fills import (
    SLOT_DESTINATION_ENTITY,
    hydrate_classification_from_slot_fills,
    record_clarification_slot_fill,
    sync_slot_fills_from_plan_clarifications,
)


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        salesforce_connections=[SalesforceConnectionRef(name="SF 1", token_id="sf-tok-1")],
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-gs", name="Archian AI"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="Lead Data Export",
                sheet_name="Sheet1",
                spreadsheet_id="sheet-lead-1",
                google_sheets_connection_id="conn-gs",
            ),
        ],
    )


def _mapped_sf_job() -> JobContext:
    return JobContext(
        job_id="job-mapped",
        pipeline_stage="migrated",
        last_completed_step="migrate",
        mapping_ready=True,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Lead",
            connector_id="sf-tok-1",
        ),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Lead",
            connector_id="sf-tok-2",
        ),
    )


def test_apply_workbook_pins_records_destination_entity_slot():
    cls = {
        "destination": "google_sheets",
        "destination_name": "Lead Data Export",
        "destination_connector_id": "conn-gs",
        "google_sheets_connection_id": "conn-gs",
        "destination_spreadsheet_id": "sheet-lead-1",
        "destination_sheet_name": "Sheet1",
        "destination_object_api_name": "sheet-lead-1::Sheet1",
        "planner_destination_entity_confirmed": True,
        "planner_workbook_picker_done": True,
        "_destination_endpoint_clarified": True,
    }
    fills = record_clarification_slot_fill(
        {},
        "pick_workbook",
        classification=cls,
        option_id="gs_sheet-lead-1_Sheet1",
    )
    assert fills[SLOT_DESTINATION_ENTITY]["entity_key"] == "sheet-lead-1::Sheet1"
    assert fills[SLOT_DESTINATION_ENTITY]["confirmed"] is True


def test_hydrate_classification_from_entity_slot_fill():
    fills = {
        SLOT_DESTINATION_ENTITY: {
            "confirmed": True,
            "entity_key": "sheet-lead-1::Sheet1",
            "spreadsheet_id": "sheet-lead-1",
            "sheet_name": "Sheet1",
            "display_name": "Lead Data Export",
            "connector_type": "google_sheets",
            "clarification_type": "pick_workbook",
        }
    }
    cls = {
        "destination": "google_sheets",
        "destination_connector_id": "conn-gs",
        "_slot_fills": fills,
    }
    plan = ExecutionPlan(steps=[], metadata={"slot_fills": fills})
    out = hydrate_classification_from_slot_fills(cls, plan=plan)
    assert out.get("destination_object_api_name") == "sheet-lead-1::Sheet1"
    assert out.get("destination_name") == "Lead Data Export"
    assert out.get("planner_destination_entity_confirmed") is True


def test_clarification_resume_skips_pin_clearing():
    job = _mapped_sf_job()
    goal = {"operations": ["map", "migrate"], "destination_phrase": "Google Sheets"}
    cls = {
        "destination": "google_sheets",
        "destination_name": "Lead Data Export",
        "destination_connector_id": "conn-gs",
        "destination_object_api_name": "sheet-lead-1::Sheet1",
        "destination_spreadsheet_id": "sheet-lead-1",
        "planner_destination_entity_confirmed": True,
        "_resume_mode": RESUME_MODE_CLARIFICATION,
    }
    out = apply_continuation_endpoint_pins_from_job(cls, goal, job, catalog=_catalog())
    assert out.get("destination_name") == "Lead Data Export"
    assert out.get("destination_object_api_name") == "sheet-lead-1::Sheet1"


def test_scope_turn_still_clears_stale_job_destination_pins():
    job = _mapped_sf_job()
    goal = {"operations": ["map", "migrate"], "destination_phrase": "Google Sheets"}
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 2",
        "destination_connector_id": "sf-tok-2",
        "destination_object_api_name": "Lead",
        "_resume_mode": RESUME_MODE_SCOPE_TURN,
    }
    out = apply_continuation_endpoint_pins_from_job(cls, goal, job, catalog=_catalog())
    assert out.get("destination") is None
    assert out.get("destination_object_api_name") is None


def test_clarification_resume_skips_destination_llm_when_connector_satisfied():
    job = _mapped_sf_job()
    goal = {"operations": ["map", "migrate"], "destination_phrase": "Google Sheets", "needs_destination": True}
    cls = {
        "destination": "google_sheets",
        "destination_connector_id": "conn-gs",
        "google_sheets_connection_id": "conn-gs",
        "_destination_endpoint_clarified": True,
        "_destination_connector_validated": True,
        "destination_spreadsheet_id": "sheet-lead-1",
        "destination_object_api_name": "sheet-lead-1::Sheet1",
        "destination_name": "Lead Data Export",
        "planner_destination_entity_confirmed": True,
        "_resume_mode": RESUME_MODE_CLARIFICATION,
    }
    assert continuation_needs_destination_llm_resolution(
        cls, goal, job, catalog=_catalog()
    ) is False


def test_scope_turn_still_needs_llm_when_phrase_changes_system():
    job = _mapped_sf_job()
    goal = {"operations": ["map", "migrate"], "destination_phrase": "Google Sheets", "needs_destination": True}
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 2",
        "destination_connector_id": "sf-tok-2",
        "_destination_endpoint_clarified": True,
        "destination_object_api_name": "Lead",
        "_resume_mode": RESUME_MODE_SCOPE_TURN,
    }
    assert continuation_needs_destination_llm_resolution(
        cls, goal, job, catalog=_catalog()
    ) is True


def test_enrich_continuation_skips_llm_on_clarification_resume():
    async def _run():
        job = _mapped_sf_job()
        goal = {"operations": ["map", "migrate"], "destination_phrase": "Google Sheets", "needs_destination": True}
        cls = {
            "_continuation_steps": ["map", "migrate"],
            "destination": "google_sheets",
            "destination_connector_id": "conn-gs",
            "google_sheets_connection_id": "conn-gs",
            "_destination_endpoint_clarified": True,
            "destination_spreadsheet_id": "sheet-lead-1",
            "destination_sheet_name": "Sheet1",
            "destination_object_api_name": "sheet-lead-1::Sheet1",
            "destination_name": "Lead Data Export",
            "planner_destination_entity_confirmed": True,
            "_resume_mode": RESUME_MODE_CLARIFICATION,
        }
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(),
        ) as mock_llm:
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="migrate extracted data to google sheet",
                catalog=_catalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
        mock_llm.assert_not_called()
        assert out.get("destination_name") == "Lead Data Export"
        assert out.get("destination_object_api_name") == "sheet-lead-1::Sheet1"

    asyncio.run(_run())


def test_sync_slot_fills_from_selected_workbook_clarification():
    from services.planning.schemas import Clarification

    cls = {
        "destination": "google_sheets",
        "destination_name": "Lead Data Export",
        "destination_connector_id": "conn-gs",
        "destination_spreadsheet_id": "sheet-lead-1",
        "destination_sheet_name": "Sheet1",
        "destination_object_api_name": "sheet-lead-1::Sheet1",
        "planner_destination_entity_confirmed": True,
    }
    clar = Clarification(
        id="c1",
        clarification_type="pick_workbook",
        prompt="Which workbook?",
        required=True,
        selected_option_id="gs_sheet-lead-1_Sheet1",
    )
    plan = ExecutionPlan(steps=[], clarifications=[clar])
    _, plan = sync_slot_fills_from_plan_clarifications(plan, cls)
    fills = plan.metadata.get("slot_fills") or {}
    assert fills[SLOT_DESTINATION_ENTITY]["entity_key"] == "sheet-lead-1::Sheet1"
