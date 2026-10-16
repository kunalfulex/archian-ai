"""Google Sheets mapping entry preparation (combine dedupe + orphan cleanup)."""
from services.gsheets_migration_service import GoogleSheetsMigrationService
from services.planning.flow_steps import apply_mapping_transformation_to_config


def test_apply_mapping_transformation_removes_orphan_name():
    mapping = {
        "name": {"sheet": "Sheet1", "column": "Name", "is_duplicate_check": False},
        "first_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True, "combine_group": "name", "combine_order": 0},
        "last_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True, "combine_group": "name", "combine_order": 1},
        "email": {"sheet": "Sheet1", "column": "Email", "is_duplicate_check": True},
    }
    rules = {
        "fields": [
            {
                "action": "combine",
                "source_fields": ["first_name", "last_name"],
                "output_field": "Name",
                "output_sheet": "Sheet1",
                "separator": " ",
            }
        ]
    }
    cleaned = apply_mapping_transformation_to_config(mapping, rules, destination="google_sheets")
    assert "name" not in cleaned
    assert "first_name" in cleaned
    assert "last_name" in cleaned


def test_prepare_mapping_entries_dedupes_name_column():
    svc = GoogleSheetsMigrationService()
    mapping = {
        "name": {"sheet": "Sheet1", "column": "Name"},
        "first_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True, "combine_group": "name", "combine_order": 0},
        "last_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True, "combine_group": "name", "combine_order": 1},
        "email": {"sheet": "Sheet1", "column": "Email", "is_duplicate_check": True},
    }
    entries, columns, key_columns = svc._prepare_mapping_entries(mapping)
    name_entries = [e for e in entries if e["column"] == "Name"]
    assert len(name_entries) == 1
    assert name_entries[0]["field"] in ("first_name", "last_name", "name")
    assert key_columns == ["Email"]
    assert columns.count("Name") == 1


def test_track_migration_records_uses_built_row_values():
    svc = GoogleSheetsMigrationService()
    mapping_entries = [
        {"field": "email", "column": "Email", "position": 0, "is_duplicate_check": True, "is_combined_field": False},
    ]
    headers = ["Email", "Name"]
    header_index = {h: i for i, h in enumerate(headers)}
    row_values = svc._build_row_values(
        {"email": "a@b.com"},
        mapping_entries,
        headers,
        header_index,
    )
    assert row_values[header_index["Email"]] == "a@b.com"
