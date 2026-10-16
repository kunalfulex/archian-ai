"""Tests for cross-system extract field scoping."""
from __future__ import annotations

from services.planning.extract_field_scope import (
    apply_cross_system_field_guardrail,
    build_extract_field_scope,
    extract_portability_context,
    filter_field_metadata_for_scope,
    filter_gsheets_columns_for_scope,
    filter_sql_referenced_columns,
    guardrail_soql_for_cross_system,
    infer_cross_system_from_extract_context,
    is_reference_base_field,
)


def test_infer_cross_system_sf_different_orgs():
    assert infer_cross_system_from_extract_context(
        source_type="salesforce",
        destination="salesforce",
        source_name="SF 1",
        destination_name="SF 2",
    )


def test_extract_portability_context_from_classification():
    ctx = extract_portability_context(
        {
            "source": "salesforce",
            "destination": "sql",
            "source_name": "SF 1",
            "destination_name": "Test DB",
        }
    )
    assert ctx["heterogeneous"] is True
    assert ctx["source_type"] == "salesforce"
    assert ctx["destination_type"] == "sql"


def test_build_scope_from_migration_intent():
    scope = build_extract_field_scope(
        {
            "cross_system": True,
            "include_related_data": True,
            "parent_entity_key": "Account",
            "field_hints": ["name"],
        }
    )
    assert scope["cross_system"] is True
    assert "Account" in scope["parent_entity_keys"]


def test_extract_field_scope_blocks_relationship_without_include_related():
    fields = [
        {"api_name": "FirstName", "label": "First Name"},
        {"api_name": "Account.Name", "label": "Account Name", "is_relationship_field": True},
    ]
    scope = build_extract_field_scope(
        {
            "cross_system": True,
            "include_related_data": False,
            "destination_entity_key": "Account",
        }
    )
    filtered = filter_field_metadata_for_scope(fields, scope)
    apis = {f["api_name"] for f in filtered}
    assert "FirstName" in apis
    assert "Account.Name" not in apis


def test_filter_metadata_removes_lookup_and_id():
    fields = [
        {"api_name": "FirstName", "label": "First Name", "is_lookup": False},
        {"api_name": "AccountId", "label": "Account ID", "is_lookup": True},
        {"api_name": "Id", "label": "Record ID", "is_lookup": False},
        {"api_name": "Account.Name", "label": "Account Name", "is_relationship_field": True},
    ]
    scope = build_extract_field_scope(
        {"cross_system": True, "include_related_data": True, "parent_entity_key": "Account"}
    )
    filtered = filter_field_metadata_for_scope(fields, scope)
    apis = {f["api_name"] for f in filtered}
    assert "FirstName" in apis
    assert "Account.Name" in apis
    assert "AccountId" not in apis
    assert "Id" not in apis


def test_guardrail_strips_id_from_selected_fields():
    scope = build_extract_field_scope({"cross_system": True})
    meta = [
        {"api_name": "FirstName"},
        {"api_name": "AccountId", "is_lookup": True},
        {"api_name": "Account.Name", "is_relationship_field": True},
    ]
    kept = apply_cross_system_field_guardrail(
        ["Id", "FirstName", "AccountId", "Account.Name"],
        scope,
        field_metadata=meta,
        prompt="extract contacts with account name",
    )
    assert "FirstName" in kept
    assert "Id" not in kept
    assert "AccountId" not in kept


def test_soql_guardrail_rebuilds_select():
    scope = build_extract_field_scope(
        {"cross_system": True, "include_related_data": True, "parent_entity_key": "Account"}
    )
    soql = "SELECT Id, FirstName, AccountId, Account.Name FROM Contact LIMIT 10"
    meta = [
        {"api_name": "FirstName"},
        {"api_name": "AccountId", "is_lookup": True},
    ]
    cleaned, fields = guardrail_soql_for_cross_system(
        soql,
        scope,
        field_metadata=meta,
        prompt="contacts with account name",
    )
    assert "AccountId" not in cleaned
    assert "Id" not in cleaned
    assert "FirstName" in cleaned
    assert "Account.Name" in cleaned
    assert "FirstName" in fields


def test_sql_reference_column_filter():
    scope = build_extract_field_scope({"cross_system": True})
    cols = ["first_name", "email", "customer_id", "account_uuid"]
    kept = filter_sql_referenced_columns(cols, scope)
    assert kept == ["first_name", "email"]


def test_gsheets_id_column_filter():
    scope = build_extract_field_scope({"cross_system": True})
    cols = ["First Name", "Email", "Salesforce Id", "Record ID"]
    kept = filter_gsheets_columns_for_scope(cols, scope)
    assert "First Name" in kept
    assert "Email" in kept
    assert "Salesforce Id" not in kept


def test_reference_base_field_heuristic():
    assert is_reference_base_field("AccountId", {"is_lookup": True})
    assert is_reference_base_field("OwnerId")
    assert not is_reference_base_field("FirstName")
