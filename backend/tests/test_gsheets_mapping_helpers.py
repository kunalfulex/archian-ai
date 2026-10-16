"""Tests for Google Sheets primary sheet resolution."""

from utils.gsheets_mapping_helpers import resolve_gsheets_primary_sheet


def test_prefers_explicit_destination_sheet():
    structure = {
        "Sheet1": {"columns": {"A": {}, "B": {}, "C": {}}},
        "Contacts": {"columns": {"Email": {}}},
    }
    assert resolve_gsheets_primary_sheet(structure, destination_sheet_name="Contacts") == "Contacts"


def test_falls_back_to_sheet_with_most_columns():
    structure = {
        "Sheet1": {"columns": {"A": {}}},
        "Wide": {"columns": {"A": {}, "B": {}, "C": {}}},
    }
    assert resolve_gsheets_primary_sheet(structure) == "Wide"
