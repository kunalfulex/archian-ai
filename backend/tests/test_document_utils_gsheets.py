"""Tests for Google Sheets category parsing in document_utils."""

import asyncio
from collections.abc import Mapping

from db.extracted_schema import _parse_gsheets_header_to_columns


class FakeRecord(Mapping):
    """Mimics databases.Record: mapping protocol without .get()."""

    def __init__(self, data):
        self._data = dict(data)

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)


def test_parse_gsheets_header_to_columns_from_dict_jsonb():
    raw = {"headers": [{"name": "Email"}, {"name": "First Name"}]}
    cols = _parse_gsheets_header_to_columns(raw)
    assert list(cols.keys()) == ["Email", "First Name"]


def test_get_dynamic_document_type_categories_gsheets_record_row(monkeypatch):
    """Database Record-like rows without .get() should not break GSheets category fetch."""
    from utils import document_utils

    row = FakeRecord(
        {
            "spreadsheet_id": "abc123",
            "spreadsheet_name": "Salesforce Contacts",
            "sheet_name": "Sheet1",
            "header_schema": {"headers": [{"name": "Email"}, {"name": "LastName"}]},
            "ai_categorization": None,
            "google_sheets_connection_id": "conn-1",
        }
    )

    async def fake_fetch_all(query, values=None):
        return [row]

    async def fake_fetch_semantics_map(**kwargs):
        return {}

    monkeypatch.setattr(document_utils.database, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(document_utils, "fetch_semantics_map", fake_fetch_semantics_map)

    categories = asyncio.run(
        document_utils.get_dynamic_document_type_categories(
            user_id="user-1",
            destination="google_sheets",
        )
    )

    assert len(categories) == 1
    assert categories[0]["fields"] == ["Email", "LastName"]
    assert categories[0]["spreadsheet_name"] == "Salesforce Contacts"
