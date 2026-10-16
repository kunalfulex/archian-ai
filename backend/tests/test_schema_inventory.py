"""Tests for schema-aware entity inventory (SMA Phase A)."""
from services.semantic_mapping.schema_inventory import (
    EntitySchemaRecord,
    _lookup_record,
    columns_from_schema_payload,
    _is_quality_artifact_column,
)


def test_quality_artifact_columns_rejected():
    assert _is_quality_artifact_column("by_field")
    assert _is_quality_artifact_column("case_inconsistency_email")
    assert _is_quality_artifact_column("total_missing")
    assert not _is_quality_artifact_column("email")
    assert not _is_quality_artifact_column("account_id")


def test_columns_from_sql_schema_data():
    schema = {
        "columns": [
            {"column_name": "Id", "data_type": "VARCHAR", "is_nullable": False},
            {"column_name": "Email", "data_type": "VARCHAR"},
        ]
    }
    cols = columns_from_schema_payload(schema)
    names = [c.name for c in cols]
    assert "id" in names
    assert "email" in names
    assert "by_field" not in names


def test_gsheets_lookup_does_not_collide_on_sheet_tab_name():
    """Bare tab names like Sheet1 must not alias across workbooks."""
    archian = EntitySchemaRecord(label="Archian Data / Sheet1", categorization=None, columns=[])
    zen = EntitySchemaRecord(label="zen Contacts / Sheet1", categorization=None, columns=[])
    index = {
        "sid_archian::sheet1": archian,
        "sid_zen::sheet1": zen,
    }
    assert _lookup_record(index, "sid_archian::Sheet1") is archian
    assert _lookup_record(index, "sid_zen::Sheet1") is zen
    assert _lookup_record(index, "Sheet1") is None


def test_columns_from_assessment_schema_columns_only():
    payload = [
        "FirstName",
        "LastName",
        {"name": "Email", "data_type": "string"},
    ]
    cols = columns_from_schema_payload(payload)
    assert {c.name for c in cols} == {"firstname", "lastname", "email"}
