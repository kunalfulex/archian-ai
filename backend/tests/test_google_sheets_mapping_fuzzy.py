"""Exact workbook name resolution for map_to_google_sheets lookup."""

from handlers.google_sheets.mapping import _fuzzy_resolve_spreadsheet_by_name


def test_resolve_exact_workbook_name():
    catalog = [
        {
            "spreadsheet_id": "id-exact",
            "spreadsheet_name": "contact list",
            "sheet_name": "Sheet1",
        },
    ]
    row = _fuzzy_resolve_spreadsheet_by_name("contact list", catalog)
    assert row is not None
    assert row["spreadsheet_id"] == "id-exact"
