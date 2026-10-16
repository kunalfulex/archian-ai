"""Tests for rollback key column extraction."""
from utils.rollback_mapping_keys import (
    build_mapping_config_for_batch,
    duplicate_check_columns_from_mapping,
    extract_key_columns_from_mapping_config,
)


def test_duplicate_check_columns_from_google_sheets_mapping():
    mapping = {
        "FirstName": {"column": "First Name", "is_duplicate_check": False},
        "Email": {"column": "Email", "is_duplicate_check": True},
        "Phone": {"column": "Phone", "is_duplicate_check": False},
    }
    assert duplicate_check_columns_from_mapping(mapping, destination="google_sheets") == ["Email"]


def test_duplicate_check_columns_from_split_mapping():
    mapping = {
        "Name": [
            {"column": "First Name", "position": 0, "is_duplicate_check": False},
            {"column": "Last Name", "position": 1, "is_duplicate_check": False},
        ],
        "Email": {"column": "Email", "is_duplicate_check": True},
    }
    assert duplicate_check_columns_from_mapping(mapping, destination="google_sheets") == ["Email"]


def test_extract_key_columns_prefers_explicit_key_columns():
    config = {
        "spreadsheet_id": "abc",
        "key_columns": ["External Id"],
        "mapping": {"Email": {"column": "Email", "is_duplicate_check": True}},
    }
    assert extract_key_columns_from_mapping_config(config, destination="google_sheets") == [
        "External Id"
    ]


def test_extract_key_columns_from_mapping_flags():
    config = {
        "mapping": {
            "Email": {"column": "Email", "is_duplicate_check": True},
            "Phone": {"column": "Phone", "is_duplicate_check": False},
        }
    }
    assert extract_key_columns_from_mapping_config(config, destination="google_sheets") == [
        "Email"
    ]


def test_extract_key_columns_infers_email_from_sample_row():
    config = {"spreadsheet_id": "abc", "field_count": 3}
    sample = {"Email": "a@b.com", "Phone": "", "Company": ""}
    assert extract_key_columns_from_mapping_config(
        config,
        destination="google_sheets",
        sample_row=sample,
    ) == ["Email"]


def test_build_mapping_config_for_batch_includes_mapping_and_keys():
    mapping = {
        "Email": {"column": "Email", "is_duplicate_check": True},
    }
    out = build_mapping_config_for_batch(
        mapping=mapping,
        destination="google_sheets",
        base={"spreadsheet_id": "x"},
    )
    assert out["spreadsheet_id"] == "x"
    assert out["key_columns"] == ["Email"]
    assert out["mapping"]["Email"]["column"] == "Email"
