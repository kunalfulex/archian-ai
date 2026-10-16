"""Tests for unified catalog entity scoring."""
from __future__ import annotations

from services.planning.entity_scoring import (
    rank_salesforce_catalog,
    rank_sql_catalog,
    score_catalog_entity,
)


def test_sql_metadata_table_penalized_for_user_data_prompt():
    semantics_users = {
        "primary": {"category": "Users", "description": "Application user accounts"},
        "synonyms": ["user table", "users"],
        "entity_class": "business",
    }
    semantics_meta = {
        "primary": {"category": "System Catalog"},
        "entity_class": "metadata",
    }
    score_users = score_catalog_entity(
        entity_key="users",
        label="Users",
        prompt="extract user data from Test DB",
        hints=["user data"],
        semantics=semantics_users,
        connector_type="sql",
        catalog=[
            {"label": "Users", "object_api_name": "users"},
            {"label": "SQL Extracted Schemas", "object_api_name": "sql_extracted_schemas"},
        ],
    )
    score_meta = score_catalog_entity(
        entity_key="sql_extracted_schemas",
        label="SQL Extracted Schemas",
        prompt="extract user data from Test DB",
        hints=["user data"],
        semantics=semantics_meta,
        connector_type="sql",
        catalog=[
            {"label": "Users", "object_api_name": "users"},
            {"label": "SQL Extracted Schemas", "object_api_name": "sql_extracted_schemas"},
        ],
    )
    assert score_users.score > score_meta.score


def test_rank_sql_catalog_users_beats_sql_extracted_schemas():
    tables = [
        {"table_name": "users", "label": "Users"},
        {"table_name": "sql_extracted_schemas", "label": "SQL Extracted Schemas"},
    ]
    semantics_map = {
        "users": {
            "primary": {"category": "Users"},
            "synonyms": ["user data"],
            "entity_class": "business",
        },
        "sql_extracted_schemas": {
            "primary": {"category": "System Catalog"},
            "entity_class": "metadata",
        },
    }
    ranked = rank_sql_catalog(
        "extract user data from Test DB",
        tables,
        ["user data"],
        semantics_map=semantics_map,
        connector_type="sql",
    )
    assert ranked
    assert ranked[0][0] == "users"


def test_rank_gsheets_business_tab_beats_registry_tab():
    from services.planning.entity_scoring import rank_gsheets_catalog

    sheets = [
        {
            "spreadsheet_id": "abc123",
            "sheet_name": "Customers",
            "label": "Customers",
            "entity_key": "abc123::Customers",
        },
        {
            "spreadsheet_id": "abc123",
            "sheet_name": "user_google_spreadsheets",
            "label": "User Google Spreadsheets",
            "entity_key": "abc123::user_google_spreadsheets",
        },
    ]
    semantics_map = {
        "abc123::Customers": {
            "primary": {"category": "Customers"},
            "entity_class": "business",
        },
        "abc123::user_google_spreadsheets": {
            "primary": {"category": "Spreadsheet Registry"},
            "entity_class": "metadata",
        },
    }
    ranked = rank_gsheets_catalog(
        "extract customer data from spreadsheet",
        sheets,
        ["customer data"],
        semantics_map=semantics_map,
    )
    assert ranked
    assert ranked[0][1] == "Customers"


def test_filter_metadata_for_vague_user_data_prompt():
    from services.planning.binding.retrieve import _filter_metadata_for_vague_user_data

    ranked = [
        (10.0, {"entity_key": "sql_extracted_schemas", "api_name": "sql_extracted_schemas"}, []),
        (8.0, {"entity_key": "users", "api_name": "users"}, []),
    ]
    semantics_map = {
        "sql_extracted_schemas": {"entity_class": "metadata"},
        "users": {"entity_class": "business", "primary": {"category": "Users"}},
    }
    filtered = _filter_metadata_for_vague_user_data(
        ranked,
        prompt="extract user data from Test DB",
        connector_type="sql",
        semantics_map=semantics_map,
    )
    keys = [row[1]["entity_key"] for row in filtered]
    assert "sql_extracted_schemas" not in keys
    assert "users" in keys


def test_user_table_prefers_users_over_google_spreadsheets_without_special_case():
    semantics_users = {
        "primary": {"category": "Users", "description": "Application user accounts"},
        "synonyms": ["user table", "users"],
    }
    semantics_ugs = {
        "primary": {"category": "User Google Spreadsheets"},
        "synonyms": ["google spreadsheets"],
    }
    score_users = score_catalog_entity(
        entity_key="users",
        label="Users",
        prompt="extract first name, last name, email from user table from Test DB",
        hints=["user table"],
        semantics=semantics_users,
        catalog=[
            {"label": "Users", "object_api_name": "users"},
            {"label": "User Google Spreadsheets", "object_api_name": "user_google_spreadsheets"},
        ],
    )
    score_ugs = score_catalog_entity(
        entity_key="user_google_spreadsheets",
        label="User Google Spreadsheets",
        prompt="extract first name, last name, email from user table from Test DB",
        hints=["user table"],
        semantics=semantics_ugs,
        catalog=[
            {"label": "Users", "object_api_name": "users"},
            {"label": "User Google Spreadsheets", "object_api_name": "user_google_spreadsheets"},
        ],
    )
    assert score_users.score > score_ugs.score


def test_exact_contact_beats_entitlement_contact():
    catalog = [
        {"object_api_name": "Contact", "label": "Contact"},
        {"object_api_name": "EntitlementContact", "label": "Entitlement Contact"},
    ]
    ranked = rank_salesforce_catalog(
        "map the data to SF 1 Contact object",
        catalog,
        ["Contact"],
    )
    assert ranked[0][0] == "Contact"
    assert ranked[0][2] > ranked[1][2]


def test_rank_salesforce_prefers_service_appointment_over_category():
    catalog = [
        {"object_api_name": "AppointmentCategory", "label": "Appointment Category"},
        {"object_api_name": "ServiceAppointment", "label": "Service Appointment"},
    ]
    semantics = {
        "ServiceAppointment": {
            "primary": {"category": "Service Appointment"},
            "synonyms": ["appointments", "field visits"],
        }
    }
    ranked = rank_salesforce_catalog(
        "deploy Appointments from SF 1 to SF 2",
        catalog,
        ["Appointments"],
        semantics_map=semantics,
    )
    assert ranked[0][0] == "ServiceAppointment"


def test_sql_table_exact_hint_match():
    tables = [
        {"table_name": "users"},
        {"table_name": "user_google_spreadsheets"},
    ]
    ranked = rank_sql_catalog(
        "extract from user table",
        tables,
        ["user table"],
        semantics_map={
            "users": {"primary": {"category": "Users"}, "synonyms": ["user table"]},
        },
    )
    assert ranked[0][0] == "users"
