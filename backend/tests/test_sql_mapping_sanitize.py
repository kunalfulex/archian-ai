"""Tests for SQL mapping lookup_of sanitization."""

from utils.sql_mapping_sanitize import (
    is_valid_sql_lookup_of,
    sanitize_sql_mapping_for_migration,
    should_drop_sql_lookup_of,
)


def _users_plans_structure() -> dict:
    return {
        "user_plans": {
            "columns": {"user_id": {}, "plan_type": {}},
            "foreign_keys": [
                {
                    "column_name": "user_id",
                    "referenced_table": "users",
                    "referenced_column": "id",
                }
            ],
        },
        "users": {
            "columns": {"id": {}, "email": {}, "first_name": {}},
            "foreign_keys": [],
        },
    }


def test_drop_same_table_lookup():
    drop, reason = should_drop_sql_lookup_of("users", "users.user_id", "users")
    assert drop is True
    assert reason == "same_table_as_lookup_child"


def test_drop_internal_extracted_schemas_lookup():
    drop, reason = should_drop_sql_lookup_of("extracted_schemas", "extracted_schemas.user_id")
    assert drop is True
    assert "internal_table_ref" in reason


def test_drop_lookup_on_primary_table_direct_field():
    drop, reason = should_drop_sql_lookup_of(
        "users",
        "contacts.user_id",
        "users",
        tables_structure=_users_plans_structure(),
    )
    assert drop is True
    assert reason == "primary_table_direct_field"


def test_keep_valid_cross_table_lookup_with_schema():
    structure = _users_plans_structure()
    drop, reason = should_drop_sql_lookup_of(
        "users",
        "user_plans.user_id",
        "user_plans",
        tables_structure=structure,
    )
    assert drop is False
    assert reason == ""
    assert is_valid_sql_lookup_of("users", "user_plans.user_id", "user_plans", structure)


def test_drop_invalid_cross_table_lookup_with_schema():
    structure = _users_plans_structure()
    drop, reason = should_drop_sql_lookup_of(
        "users",
        "contacts.user_id",
        "user_plans",
        tables_structure=structure,
    )
    assert drop is True
    assert reason == "invalid_lookup_relationship"


def test_sanitize_removes_self_lookup_from_mapping():
    mapping = {
        "Email": {"table": "users", "column": "email", "lookup_of": "users.user_id"},
        "Name": [
            {"table": "users", "column": "first_name", "lookup_of": "users.user_id"},
            {"table": "users", "column": "last_name", "lookup_of": "users.user_id"},
        ],
    }
    out = sanitize_sql_mapping_for_migration(mapping, "users")
    assert "lookup_of" not in out["Email"]
    assert all("lookup_of" not in item for item in out["Name"])


def test_sanitize_remaps_internal_table_name():
    mapping = {
        "Email": {
            "table": "extracted_schemas",
            "column": "email",
            "lookup_of": "extracted_schemas.user_id",
        },
    }
    out = sanitize_sql_mapping_for_migration(mapping, "users")
    assert out["Email"]["table"] == "users"
    assert "lookup_of" not in out["Email"]


def test_sanitize_primary_destination_strips_spurious_lookup():
    """Regression: extract→users must not keep contacts.user_id on users columns."""
    mapping = {
        "first_name": {
            "table": "users",
            "column": "first_name",
            "lookup_of": "contacts.user_id",
        },
        "email": {"table": "users", "column": "email"},
    }
    out = sanitize_sql_mapping_for_migration(
        mapping,
        "users",
        tables_structure=_users_plans_structure(),
    )
    assert "lookup_of" not in out["first_name"]
