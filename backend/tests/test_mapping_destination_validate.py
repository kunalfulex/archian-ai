from utils.mapping_destination_validate import (
    MappingValidationPhase,
    filter_google_sheets_mapping_to_destination_schema,
    filter_salesforce_mapping_to_destination_schema,
    filter_sql_mapping_to_destination_schema,
    normalize_gsheets_mapping_source_keys,
    source_field_matches_destination_column,
)


def test_phone_does_not_match_first_name_column():
    assert source_field_matches_destination_column("phone", "FirstName") is False
    assert source_field_matches_destination_column("phone", "Phone") is True


def test_first_name_matches_first_name_column():
    assert source_field_matches_destination_column("first_name", "FirstName") is True
    assert source_field_matches_destination_column("last_name", "LastName") is True


def test_migrate_phase_keeps_user_cross_column_mapping():
    sheets = {
        "Sheet1": {
            "columns": {
                "Email": {"type": "string"},
                "Address": {"type": "string"},
            }
        }
    }
    mapping = {
        "MobilePhone": {"column": "Address", "sheet": "Sheet1"},
    }
    auto = filter_google_sheets_mapping_to_destination_schema(
        mapping, sheets, default_sheet="Sheet1", phase=MappingValidationPhase.AUTO_MAP
    )
    migrate = filter_google_sheets_mapping_to_destination_schema(
        mapping, sheets, default_sheet="Sheet1", phase=MappingValidationPhase.MIGRATE
    )
    assert "MobilePhone" not in auto
    assert "MobilePhone" in migrate
    assert migrate["MobilePhone"]["column"] == "Address"


def test_filter_drops_unmapped_source_fields():
    sheets = {
        "Sheet1": {
            "columns": {
                "Email": {"type": "string"},
                "FirstName": {"type": "string"},
                "LastName": {"type": "string"},
                "Company Name": {"type": "string"},
            }
        }
    }
    mapping = {
        "email": {"column": "Email", "sheet": "Sheet1", "is_duplicate_check": True},
        "phone": {"column": "FirstName", "sheet": "Sheet1", "is_duplicate_check": False},
        "first_name": {"column": "FirstName", "sheet": "Sheet1", "is_duplicate_check": True},
        "last_name": {"column": "LastName", "sheet": "Sheet1", "is_duplicate_check": True},
    }
    out = filter_google_sheets_mapping_to_destination_schema(
        mapping, sheets, default_sheet="Sheet1"
    )
    assert "phone" not in out
    assert "email" in out
    assert "first_name" in out


def test_normalize_rekeys_destination_labels_to_source_fields():
    flat_data = {
        "email": "a@b.com",
        "phone": "555-1234",
        "first_name": "Ada",
        "last_name": "Lovelace",
    }
    sheets = {
        "Sheet1": {
            "columns": {
                "Email": {"type": "string"},
                "Mobile Number": {"type": "string"},
                "Contact Number": {"type": "string"},
                "Name": {"type": "string"},
            }
        }
    }
    mapping = {
        "Email": {"column": "Email", "sheet": "Sheet1", "is_duplicate_check": True},
        "Mobile Number": {"column": "Mobile Number", "sheet": "Sheet1"},
        "Contact Number": {"column": "Contact Number", "sheet": "Sheet1"},
        "first_name": {"column": "Name", "sheet": "Sheet1"},
        "last_name": {"column": "Name", "sheet": "Sheet1"},
    }
    out = normalize_gsheets_mapping_source_keys(mapping, flat_data, sheets, default_sheet="Sheet1")
    assert "Email" not in out
    assert "Mobile Number" not in out
    assert "Contact Number" not in out
    assert out["email"]["column"] == "Email"
    assert out["phone"]["column"] in ("Mobile Number", "Contact Number")
    assert "first_name" in out
    assert "last_name" in out


def test_salesforce_filter_drops_fields_not_in_org_schema():
    objects = {
        "Contact": {
            "fields": {
                "Email": {"label": "Email"},
                "FirstName": {"label": "First Name"},
                "LastName": {"label": "Last Name"},
            }
        }
    }
    mapping = {
        "email": {"object": "Contact", "field": "Email"},
        "phone": {"object": "Contact", "field": "Phone__c"},
        "first_name": {"object": "Contact", "field": "FirstName"},
    }
    out = filter_salesforce_mapping_to_destination_schema(mapping, objects)
    assert "phone" not in out
    assert "email" in out
    assert "first_name" in out


def test_sql_filter_drops_invalid_columns():
    tables = {
        "users": {
            "columns": {
                "email": {"type": "varchar"},
                "first_name": {"type": "varchar"},
                "last_name": {"type": "varchar"},
            }
        }
    }
    mapping = {
        "email": {"table": "users", "column": "email"},
        "phone": {"table": "users", "column": "first_name"},
        "first_name": {"table": "users", "column": "first_name"},
    }
    out = filter_sql_mapping_to_destination_schema(
        mapping, tables, primary_table="users"
    )
    assert "phone" not in out
    assert "email" in out
    assert "first_name" in out
