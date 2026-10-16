"""Tests for multi Google account connection helpers."""
from __future__ import annotations

import pytest

from services.google_sheets_connection_service import (
    ambiguous_workbook_response,
    found_workbook_response,
    _dedupe_workbook_candidates,
    _connection_label,
    resolve_workbook_by_name,
)
from services.labeling.entity_labeling_service import _gsheet_entity_display_name


def test_gsheet_entity_display_name_enriched():
    assert _gsheet_entity_display_name("Employee list", "Sheet1") == "Employee list · Sheet1"
    assert _gsheet_entity_display_name("Sheet1", "Sheet1") == "Sheet1"
    assert _gsheet_entity_display_name(None, "Sheet1") == "Sheet1"


def test_dedupe_workbook_candidates():
    matches = [
        {"spreadsheet_id": "a", "google_sheets_connection_id": "1"},
        {"spreadsheet_id": "a", "google_sheets_connection_id": "1"},
        {"spreadsheet_id": "b", "google_sheets_connection_id": "2"},
    ]
    assert len(_dedupe_workbook_candidates(matches)) == 2


def test_ambiguous_workbook_response_shape():
    matches = [
        {
            "spreadsheet_id": "sid1",
            "spreadsheet_name": "Contacts",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "c1",
            "google_account_email": "a@example.com",
            "connection_label": "Work",
        },
        {
            "spreadsheet_id": "sid2",
            "spreadsheet_name": "Contacts",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "c2",
            "google_account_email": "b@example.com",
            "connection_label": "Personal",
        },
    ]
    out = ambiguous_workbook_response("Contacts", matches)
    assert out["found"] is False
    assert out["error"] == "ambiguous_spreadsheet_name"
    assert len(out["candidates"]) == 2
    assert "a@example.com" in out["message"]


def test_found_workbook_response_includes_connection():
    match = {
        "spreadsheet_id": "sid",
        "spreadsheet_name": "Leads",
        "sheet_name": "Sheet1",
        "google_sheets_connection_id": "conn-1",
        "google_account_email": "user@test.com",
    }
    out = found_workbook_response(match, source="test")
    assert out["found"] is True
    assert out["google_sheets_connection_id"] == "conn-1"
    assert out["source"] == "test"


def test_connection_label_prefers_custom_name():
    assert _connection_label({"connection_name": "Marketing", "google_account_email": "a@b.com"}) == "Marketing"
    assert _connection_label({"connection_name": "Google Sheets", "google_account_email": "a@b.com"}) == "a@b.com"


def test_resolve_workbook_by_name_parses_email_suffix(monkeypatch):
    async def fake_list(user_id, name, sheet_name=None):
        return [
            {
                "spreadsheet_id": "sid1",
                "spreadsheet_name": "Contacts",
                "sheet_name": "Sheet1",
                "google_sheets_connection_id": "c1",
                "google_account_email": "work@example.com",
                "connection_label": "work@example.com",
            },
            {
                "spreadsheet_id": "sid2",
                "spreadsheet_name": "Contacts",
                "sheet_name": "Sheet1",
                "google_sheets_connection_id": "c2",
                "google_account_email": "home@example.com",
                "connection_label": "home@example.com",
            },
        ]

    monkeypatch.setattr(
        "services.google_sheets_connection_service.list_workbook_matches",
        fake_list,
    )

    import asyncio

    result = asyncio.run(resolve_workbook_by_name("user-1", "Contacts (work@example.com)"))
    assert result["found"] is True
    assert result["google_sheets_connection_id"] == "c1"
    assert result["spreadsheet_id"] == "sid1"


def test_resolve_workbook_by_name_ambiguous_without_hint(monkeypatch):
    async def fake_list(user_id, name, sheet_name=None):
        return [
            {
                "spreadsheet_id": "sid1",
                "spreadsheet_name": "Contacts",
                "sheet_name": "Sheet1",
                "google_sheets_connection_id": "c1",
                "google_account_email": "a@example.com",
                "connection_label": "a@example.com",
            },
            {
                "spreadsheet_id": "sid2",
                "spreadsheet_name": "Contacts",
                "sheet_name": "Sheet1",
                "google_sheets_connection_id": "c2",
                "google_account_email": "b@example.com",
                "connection_label": "b@example.com",
            },
        ]

    monkeypatch.setattr(
        "services.google_sheets_connection_service.list_workbook_matches",
        fake_list,
    )

    import asyncio

    result = asyncio.run(resolve_workbook_by_name("user-1", "Contacts"))
    assert result["found"] is False
    assert result["error"] == "ambiguous_spreadsheet_name"
    assert len(result["candidates"]) == 2
