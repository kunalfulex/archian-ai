"""Tests for Google Sheets executor and PLAN_SCOPE-driven single-op plans."""

from services.google_sheets.models import OperationType
from services.google_sheets.row_builder import infer_key_columns
from services.planning.plan_builder import build_plan_steps


def test_plan_upsert_from_plan_scope():
    plan = build_plan_steps(
        classification={
            "intent": "upsert",
            "source": "google_sheets",
            "destination": "google_sheets",
            "destination_name": "My Sheet",
            "_plan_scope": {"operations": ["upsert"]},
        },
        prompt="Add email=test@a.com to My Sheet",
    )
    assert plan.steps[0].parameters.get("tool_id") == "gsheets_execute_operation"
    assert plan.steps[0].parameters.get("operation") == "upsert"


def test_plan_formula_from_plan_scope():
    plan = build_plan_steps(
        classification={
            "intent": "formula",
            "mode": "formula",
            "source": "google_sheets",
            "_plan_scope": {"operations": ["formula"]},
        },
        prompt="Write a QUERY formula for tab 2",
    )
    assert plan.steps[0].parameters.get("tool_id") == "gsheets_execute_operation"
    assert plan.steps[0].parameters.get("operation") == "formula"


def test_plan_formula_requires_plan_scope_not_prompt_keywords():
    plan = build_plan_steps(
        classification={"intent": "general", "source": "google_sheets"},
        prompt="Write a QUERY formula for tab 2",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "gsheets_execute_operation" not in tools or (
        plan.steps and plan.steps[0].parameters.get("operation") != "formula"
    )


def test_infer_key_columns_email():
    keys = infer_key_columns(["Name", "Email Address", "Phone"])
    assert keys == ["Email Address"]


def test_upsert_completion_summary_includes_workbook_and_preview():
    from services.google_sheets.models import SpreadsheetContext, UpsertResult
    from services.google_sheets.response_summary import build_upsert_completion_chunks

    ctx = SpreadsheetContext(
        found=True,
        spreadsheet_id="abc123",
        spreadsheet_name="Employee list",
        sheet_name="Sheet1",
    )
    records = [
        {"Name": "Edison Test 2", "Email": "hello@edisonai.io"},
        {"Name": "Rose Gonzalez", "Email": "rose@edge.com"},
    ]
    result = UpsertResult(rows_inserted=27, rows_updated=0, processed_records=27)
    text = "".join(
        build_upsert_completion_chunks(
            ctx,
            result,
            records,
            ["Name", "Email"],
            key_columns=["Email"],
        )
    )
    assert "Employee list" in text
    assert "27" in text
    assert "new row(s) added" in text
    assert "Edison Test 2" in text
    assert "hello@edisonai.io" in text
    assert "docs.google.com/spreadsheets/d/abc123" in text
