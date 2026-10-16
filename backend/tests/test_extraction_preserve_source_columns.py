"""Extraction prompt must preserve Google Sheets source column names."""

import asyncio

import pytest

from handlers.prompt_handler import PromptBuilder


@pytest.mark.parametrize("source", ["google_sheets", "sql", "salesforce"])
def test_preserve_source_columns_section_for_connectors(source):
    text = PromptBuilder.get_preserve_source_columns_section(source)
    assert "Preserve source column names" in text
    assert "`Name` must remain `Name`" in text
    assert "split_2" in text


def test_preserve_source_columns_not_for_file_upload():
    assert PromptBuilder.get_preserve_source_columns_section("file") == ""


def test_build_extraction_prompt_uses_source_name_for_gsheets_classification(monkeypatch):
    captured = {}

    async def fake_categories(*, user_id, destination, destination_name):
        captured["destination_name"] = destination_name
        return [{"spreadsheet_name": "Customer Contact Details", "category": "Customer Records", "description": "x"}]

    monkeypatch.setattr(
        "utils.document_utils.get_dynamic_document_type_categories",
        fake_categories,
    )

    asyncio.run(
        PromptBuilder.build_extraction_prompt(
            destination="google_sheets",
            destination_name="contact list",
            extraction_goals="extract from customer contact details and migrate to contact list",
            sample_data=[{"Name": "Kunal Fule", "Email": "a@b.com"}],
            plan_limit_instruction="",
            user_id="user-1",
            source="google_sheets",
            source_name="Customer Contact Details",
        )
    )

    assert captured["destination_name"] == "Customer Contact Details"
