"""Catalog-grounded goal reconciliation after plan binding."""
from __future__ import annotations

from services.planning.create_metadata import should_materialize_create_step
from services.planning.goal_reconciliation import (
    catalog_proved_existing_gsheet_destination,
    reconcile_goal_after_catalog_binding,
)
from services.planning.schemas import PlanContract, PlanEntityRef


def test_catalog_proved_existing_gsheet_from_contract():
    classification = {
        "destination": "google_sheets",
        "destination_name": "Archian Data",
    }
    contract = PlanContract(
        destination=PlanEntityRef(
            connector_type="google_sheets",
            entity_key="abc123::Sheet1",
            spreadsheet_id="abc123",
            sheet_name="Sheet1",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    assert catalog_proved_existing_gsheet_destination(classification, contract)


def test_reconcile_clears_create_when_binding_proved_existing_workbook():
    classification = {
        "destination": "google_sheets",
        "destination_name": "Archian Data",
        "planner_create_spreadsheet": True,
        "operations": ["extract", "create", "map", "migrate"],
        "planner_workbook_picker_done": True,
        "_destination_endpoint_clarified": True,
        "_destination_connector_validated": True,
    }
    parsed_goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
        "data_operations": ["filter"],
    }
    contract = PlanContract(
        destination=PlanEntityRef(
            connector_type="google_sheets",
            display_name="Archian Data",
            entity_key="abc123::Sheet1",
            spreadsheet_id="abc123",
            sheet_name="Sheet1",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    out, goal, changed = reconcile_goal_after_catalog_binding(
        classification, parsed_goal, contract
    )
    assert changed is True
    assert out.get("planner_create_spreadsheet") is False
    assert goal.get("create_destination") is False
    assert "create" not in (goal.get("operations") or [])
    assert out.get("planner_destination_confirmed") is True
    assert out.get("planner_destination_entity_confirmed") is True
    assert out.get("destination_spreadsheet_id") == "abc123"
    assert out.get("destination_sheet_name") == "Sheet1"
    tokens = out.get("_pipeline_tokens") or []
    assert "create" not in tokens


def test_reconcile_noop_when_no_create_hypothesis():
    classification = {
        "destination": "google_sheets",
        "destination_spreadsheet_id": "abc123",
        "operations": ["extract", "map", "migrate"],
    }
    goal = {"operations": ["extract", "map", "migrate"], "create_destination": False}
    contract = PlanContract(
        destination=PlanEntityRef(
            connector_type="google_sheets",
            entity_key="abc123::Sheet1",
            spreadsheet_id="abc123",
            sheet_name="Sheet1",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    _, _, changed = reconcile_goal_after_catalog_binding(classification, goal, contract)
    assert changed is False


def test_should_materialize_create_false_when_spreadsheet_id_pinned():
    classification = {
        "destination_spreadsheet_id": "abc123",
        "planner_create_spreadsheet": True,
    }
    goal = {"operations": ["extract", "create", "map", "migrate"], "create_destination": True}
    assert should_materialize_create_step(classification, goal) is False
