"""Tests for Google Sheets basic info handler helpers."""

from handlers.google_sheets.streams_info import _coerce_ai_categorization_dict


def test_coerce_ai_categorization_json_null():
    assert _coerce_ai_categorization_dict("null") == {}


def test_coerce_ai_categorization_none():
    assert _coerce_ai_categorization_dict(None) == {}


def test_coerce_ai_categorization_dict():
    raw = {"document_type": "Contacts", "document_description": "CRM contacts"}
    assert _coerce_ai_categorization_dict(raw) == raw


def test_coerce_ai_categorization_json_string():
    raw = '{"document_type": "Leads"}'
    assert _coerce_ai_categorization_dict(raw) == {"document_type": "Leads"}
