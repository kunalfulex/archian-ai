"""Tests for join_ranges and context cache serialization."""

import pytest

from services.google_sheets.context_cache import context_from_dict, context_to_dict
from services.google_sheets.join_ranges import join_records
from services.google_sheets.models import SpreadsheetContext
from services.planning.plan_builder import build_plan_steps


def test_join_records_left():
    left = [{"Email": "a@b.com", "Name": "Ann"}]
    right = [{"Email": "a@b.com", "Score": 10}]
    headers, rows = join_records(
        ["Email", "Name"],
        left,
        ["Email", "Score"],
        right,
        "Email",
        "Email",
        how="left",
    )
    assert len(rows) == 1
    assert rows[0]["Name"] == "Ann"
    assert rows[0]["Score"] == 10


def test_context_roundtrip():
    ctx = SpreadsheetContext(
        found=True,
        spreadsheet_id="abc",
        spreadsheet_name="Book",
        sheet_name="Sheet1",
        headers=["Email"],
        key_columns=["Email"],
    )
    restored = context_from_dict(context_to_dict(ctx))
    assert restored.spreadsheet_id == "abc"
    assert restored.headers == ["Email"]


def test_multi_source_join_plan_two_tabs():
    plan = build_plan_steps(
        classification={
            "source": "google_sheets",
            "source_name": "Employee list",
            "source_sheet_name": "Sheet1",
            "planner_additional_sources": [
                {
                    "connector_type": "google_sheets",
                    "display_name": "Employee list",
                    "sheet_name": "Sheet2",
                }
            ],
            "multi_source_pipeline": True,
            "_plan_scope": {"operations": ["extract", "join"], "intent": "extract"},
        },
        prompt="Join Sheet1 and Sheet2 in Employee list on Email",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools.count("extract_google_sheets") == 2
    assert "gsheets_execute_operation" in tools
    join_step = next(
        s for s in plan.steps if s.parameters.get("operation") == "join"
    )
    assert join_step.parameters.get("tool_id") == "gsheets_execute_operation"


def test_multi_source_not_join_for_column_combine_service_appointments():
    prompt = (
        "Extract the 100 records from the Service Appointments sheet, combine DEALERCODE VC, "
        "DEALERBRANCHCODE VC, CUSTCODE VC column as Customer Account, and migrate to SF 1"
    )
    plan = build_plan_steps(
        classification={
            "intent": "extract",
            "source": "google_sheets",
            "destination": "salesforce",
            "_plan_scope": {"operations": ["extract", "transform", "map", "migrate"]},
        },
        prompt=prompt,
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "gsheets_execute_operation" not in tools
    assert "run_data_ops_plan" in tools


def test_multi_source_join_workbook_combine():
    plan = build_plan_steps(
        classification={
            "intent": "extract",
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "google_sheets",
            "destination_name": "contact list",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "data list workbook"}
            ],
            "multi_source_pipeline": True,
            "_plan_scope": {"operations": ["extract", "join", "map", "migrate"]},
        },
        prompt="extract from SF 1 and data list workbook, combine them and export to contact list sheet",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "gsheets_execute_operation" in tools
    assert "start_migration" in tools


def test_tab_title_from_gsheets_service_metadata():
    from services.google_sheets.executor import _tab_title_from_metadata

    assert _tab_title_from_metadata({"sheet_name": "Sheet2", "sheet_id": "1"}) == "Sheet2"
    assert _tab_title_from_metadata({"title": "Leads"}) == "Leads"


def test_enrich_does_not_turn_join_into_data_ops():
    from handlers.classification_handler import enrich_classification_for_agent_runtime

    out = enrich_classification_for_agent_runtime(
        {
            "intent": "join",
            "mode": "join",
            "source": "google_sheets",
            "_plan_scope": {"operations": ["extract", "join"]},
        },
        {"state": "extraction_completed"},
        prompt="Join Employee list Sheet1 with contact list on Email",
    )
    assert out.get("_plan_scope")
    assert out.get("operations") is None


def test_expand_formula_down():
    from services.google_sheets.formula_service import expand_formula_down

    rows = expand_formula_down("=VLOOKUP(A2, Sheet2!A:C, 2, FALSE)", "D2", 3)
    assert len(rows) == 3
    assert "A2" in rows[0][0] or "D2" in rows[0][0]
    assert "A4" in rows[2][0] or "D4" in rows[2][0]
