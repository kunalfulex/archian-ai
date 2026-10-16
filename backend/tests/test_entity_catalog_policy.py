"""Tests for user-data vs metadata entity catalog guards."""
from __future__ import annotations

from services.planning.entity_catalog_policy import (
    is_gsheets_metadata_tab_name,
    is_salesforce_metadata_object,
    is_sql_metadata_table_name,
    is_vague_user_data_migration_prompt,
    prefer_user_data_entity_from_catalog,
    should_replace_metadata_driving_entity,
)


def test_vague_user_data_prompt_detection():
    assert is_vague_user_data_migration_prompt("deploy user data to salesforce")
    assert not is_vague_user_data_migration_prompt("deploy record types to salesforce")


def test_metadata_object_detection():
    assert is_salesforce_metadata_object("RecordType")
    assert is_sql_metadata_table_name("salesforce_object_record_types")
    assert is_sql_metadata_table_name("sql_extracted_schemas")
    assert is_gsheets_metadata_tab_name("user_google_spreadsheets")
    assert not is_sql_metadata_table_name("user")


def test_prefer_users_over_sql_extracted_schemas():
    catalog = [
        {"api_name": "sql_extracted_schemas", "label": "sql_extracted_schemas"},
        {"api_name": "users", "label": "Users"},
    ]
    hit = prefer_user_data_entity_from_catalog(
        prompt="extract user data from Test DB",
        connector_type="sql",
        catalog=catalog,
    )
    assert hit == ("users", "Users")


def test_should_replace_sql_extracted_schemas():
    assert should_replace_metadata_driving_entity(
        prompt="extract user data from Test DB",
        connector_type="sql",
        entity_key="sql_extracted_schemas",
    )


def test_prefer_user_over_record_type_sf():
    catalog = [
        {"api_name": "RecordType", "label": "Record Types"},
        {"api_name": "User", "label": "User"},
        {"api_name": "Contact", "label": "Contact"},
    ]
    hit = prefer_user_data_entity_from_catalog(
        prompt="deploy user data to salesforce",
        connector_type="salesforce",
        catalog=catalog,
        current_key="RecordType",
    )
    assert hit == ("User", "User")


def test_prefer_user_table_sql():
    catalog = [
        {"api_name": "salesforce_object_record_types", "label": "salesforce_object_record_types"},
        {"api_name": "user", "label": "user"},
    ]
    hit = prefer_user_data_entity_from_catalog(
        prompt="deploy user data to salesforce",
        connector_type="sql",
        catalog=catalog,
    )
    assert hit == ("user", "user")


def test_should_replace_metadata_driving():
    assert should_replace_metadata_driving_entity(
        prompt="deploy user data to salesforce",
        connector_type="salesforce",
        entity_key="RecordType",
    )
    assert not should_replace_metadata_driving_entity(
        prompt="migrate record types to prod",
        connector_type="salesforce",
        entity_key="RecordType",
    )
