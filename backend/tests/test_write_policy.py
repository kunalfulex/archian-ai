"""Tests for migration write policy (duplicate-check driven upsert)."""
from __future__ import annotations

from services.database_migration_service import DatabaseMigrationService
from services.migration.write_policy import (
    mapping_has_duplicate_keys,
    resolve_write_policy,
)


def test_mapping_has_duplicate_keys_simple_and_split():
    assert mapping_has_duplicate_keys(
        {"Email": {"column": "email", "is_duplicate_check": True}}
    )
    assert mapping_has_duplicate_keys(
        {
            "Name": [
                {"column": "first_name", "is_duplicate_check": True},
                {"column": "last_name"},
            ]
        }
    )
    assert not mapping_has_duplicate_keys({"Email": {"column": "email"}})
    assert not mapping_has_duplicate_keys({})


def test_write_policy_sql_upsert_when_duplicate_check_cross_connector():
    mapping = {
        "Email": {"table": "users", "column": "email", "is_duplicate_check": True},
        "Phone": {"table": "users", "column": "phone", "is_duplicate_check": True},
    }
    policy = resolve_write_policy(destination="sql", mapping=mapping)
    assert policy.operation == "upsert"
    assert policy.reason == "mapping_duplicate_check"


def test_write_policy_sql_insert_without_duplicate_check():
    mapping = {"Email": {"table": "users", "column": "email"}}
    policy = resolve_write_policy(destination="sql", mapping=mapping)
    assert policy.operation == "insert"
    assert policy.reason == "default_sql"


def test_write_policy_gsheets_upsert_with_duplicate_check():
    mapping = {"Email": {"sheet": "Sheet1", "column": "Email", "is_duplicate_check": True}}
    policy = resolve_write_policy(destination="google_sheets", mapping=mapping)
    assert policy.operation == "upsert"
    assert policy.reason == "mapping_duplicate_check"


def test_write_policy_gsheets_append_without_duplicate_check():
    policy = resolve_write_policy(
        destination="google_sheets",
        mapping={"Email": {"column": "Email"}},
    )
    assert policy.operation == "append"


def test_write_policy_salesforce_default_upsert():
    policy = resolve_write_policy(destination="salesforce", mapping={})
    assert policy.operation == "upsert"


def test_write_policy_explicit_operation_wins():
    mapping = {"Email": {"column": "email", "is_duplicate_check": True}}
    policy = resolve_write_policy(
        destination="sql",
        mapping=mapping,
        explicit_operation="overwrite",
    )
    assert policy.operation == "overwrite"
    assert policy.reason == "explicit_operation"


def test_sql_determine_operation_promotes_insert_to_upsert_with_key_columns():
    svc = DatabaseMigrationService()
    assert svc._determine_operation("insert", ["email"]) == "upsert"
    assert svc._determine_operation("append", ["email"]) == "upsert"
    assert svc._determine_operation("insert", []) == "append"
    assert svc._determine_operation("overwrite", ["email"]) == "overwrite"
