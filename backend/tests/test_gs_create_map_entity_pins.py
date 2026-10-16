"""Create → map: spreadsheet create must pin GS tab entity for map_fields."""
from __future__ import annotations

from agent.session import AgentSession
from services.mapping.context import (
    build_mapping_execution_context,
    pinned_destination_entity_key,
)
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.google_sheets_destination import pin_google_sheets_destination_after_create
from services.planning.schemas import Clarification, ExecutionPlan


def test_pin_after_create_sets_tab_entity_for_mapping():
    cls = pin_google_sheets_destination_after_create(
        {
            "destination": "google_sheets",
            "destination_name": "Sales Lead Pipeline",
            "planner_create_spreadsheet": True,
            "plan_status": "approved",
        },
        spreadsheet_id="1rvyvQ6s-FDF-B-eOL7S_iCp7-eZ6-6Hm037lVLsLT30",
        sheet_name="Sheet1",
        spreadsheet_name="Sales Lead Pipeline",
    )
    assert cls["destination_sheet_name"] == "Sheet1"
    assert cls["destination_spreadsheet_id"] == "1rvyvQ6s-FDF-B-eOL7S_iCp7-eZ6-6Hm037lVLsLT30"
    assert cls["destination_object_api_name"] == "1rvyvQ6s-FDF-B-eOL7S_iCp7-eZ6-6Hm037lVLsLT30::Sheet1"
    assert pinned_destination_entity_key(classification_snapshot=cls, destination="google_sheets") == "Sheet1"
    ctx = build_mapping_execution_context(
        classification_snapshot=cls,
        destination="google_sheets",
    )
    assert ctx.has_pinned_entity
    assert ctx.mapping_target.entity_key == "Sheet1"


def test_merge_result_pins_entity_after_spreadsheet_created():
    session = AgentSession(
        user_id="u1",
        conversation_id="conv-1",
        mode="map",
        prompt="migrate to google sheet",
        classification_snapshot={
            "destination": "google_sheets",
            "destination_name": "Sales Lead Pipeline",
            "plan_status": "approved",
            "planner_create_spreadsheet": True,
            "active_job_id": "job-1",
        },
    )
    session.merge_result(
        {
            "event_type": "spreadsheet_created",
            "spreadsheet_name": "Sales Lead Pipeline",
            "spreadsheet_id": "sid-abc",
            "sheet_name": "Sheet1",
            "destination": "google_sheets",
            "destination_name": "Sales Lead Pipeline",
        }
    )
    snap = session.classification_snapshot
    assert snap["destination_spreadsheet_id"] == "sid-abc"
    assert snap["destination_sheet_name"] == "Sheet1"
    assert snap.get("planner_destination_entity_confirmed") is True
    ctx = build_mapping_execution_context(
        classification_snapshot=snap,
        destination="google_sheets",
    )
    assert ctx.has_pinned_entity


def test_create_workbook_name_does_not_confirm_destination_entity():
    plan = ExecutionPlan(
        plan_id="p-title",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-title",
                clarification_type="create_workbook_name",
                prompt="Name?",
                options=[],
            ),
        ],
    )
    out, _ = apply_clarification_selections(
        {"destination": "google_sheets", "active_job_id": "job-1"},
        plan,
        [{"clarification_id": "c-title", "text_value": "Sales Lead Pipeline"}],
    )
    assert out.get("destination_name") == "Sales Lead Pipeline"
    assert out.get("planner_spreadsheet_title_confirmed") is True
    assert out.get("planner_destination_entity_confirmed") is not True
    assert out.get("destination_object_api_name") in (None, "")
