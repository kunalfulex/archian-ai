"""Tests for SQL extract contract enforcement."""
from __future__ import annotations

from services.planning.sql_extract_contract import (
    build_sql_extract_contract,
    collect_sql_fk_relationships,
    determine_sql_columns_from_scope,
    enforce_sql_select_contract,
    resolve_sql_extract_tables,
    sql_authority_prompt_block,
    sql_fk_prompt_block,
)


def test_enforce_sql_select_contract_strips_extra_columns():
    allowed = ["first_name", "last_name", "email"]
    query = "SELECT first_name, last_name, email, company FROM users"
    enforced, kept, removed = enforce_sql_select_contract(query, allowed)
    assert "company" in removed
    assert "first_name" in enforced
    assert "email" in enforced


def test_build_sql_extract_contract_from_column_selection():
    col_result = {
        "primary_table": "users",
        "table_names": ["users"],
        "selected_columns": ["id", "email"],
        "column_metadata": [
            {"table_name": "users", "column_name": "id"},
            {"table_name": "users", "column_name": "email"},
        ],
    }
    contract = build_sql_extract_contract(col_result)
    assert contract["primary_table"] == "users"
    assert contract["allowed_select_columns"] == ["id", "email"]


def test_determine_sql_columns_from_explicit_hints():
    schemas = {"users": {"id": "int", "email": "text", "internal_flag": "bool"}}
    result = determine_sql_columns_from_scope(
        ["users"],
        schemas,
        extract_field_scope={
            "extract_field_scope": "explicit",
            "field_hints": ["email"],
        },
    )
    assert "email" in result["selected_columns"]
    assert "internal_flag" not in result["selected_columns"]


def test_resolve_sql_extract_tables_users_with_plans():
    tables = resolve_sql_extract_tables(
        table_hint="users",
        related_entity_keys=["user_plans"],
        migration_intent={
            "include_related_data": True,
            "driving_entity_key": "user_plans",
            "parent_entity_key": "users",
            "join_entity_keys": ["users", "user_plans"],
        },
    )
    assert tables == ["users", "user_plans"]


def test_collect_sql_fk_relationships_between_selected_tables():
    connector_tables = [
        {
            "table_name": "user_plans",
            "schema_data": {
                "columns": [
                    {"column_name": "user_id", "data_type": "integer"},
                    {"column_name": "plan_name", "data_type": "text"},
                ],
                "foreign_keys": [
                    {
                        "column_name": "user_id",
                        "referenced_table": "users",
                        "referenced_column": "id",
                    }
                ],
            },
        },
        {
            "table_name": "users",
            "schema_data": {
                "columns": [
                    {"column_name": "id", "data_type": "integer"},
                    {"column_name": "email", "data_type": "text"},
                ],
                "foreign_keys": [],
            },
        },
    ]
    fks = collect_sql_fk_relationships(["users", "user_plans"], connector_tables)
    assert len(fks) == 1
    assert fks[0]["child_table"] == "user_plans"
    assert fks[0]["parent_table"] == "users"
    assert fks[0]["column"] == "user_id"


def test_sql_authority_prompt_includes_fk_block():
    contract = build_sql_extract_contract(
        {
            "primary_table": "users",
            "table_names": ["users", "user_plans"],
            "selected_columns": ["email", "plan_name"],
            "fk_relationships": [
                {
                    "child_table": "user_plans",
                    "parent_table": "users",
                    "column": "user_id",
                    "referenced_column": "id",
                }
            ],
        }
    )
    block = sql_authority_prompt_block(contract)
    assert "user_plans.user_id" in block or "user_plans" in block
    assert "JOIN" in block
    assert "user_plans" in block


def test_sql_fk_prompt_block_empty_when_no_fks():
    assert sql_fk_prompt_block([]) == ""
