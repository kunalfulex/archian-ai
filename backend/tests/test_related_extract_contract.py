"""Tests for related field resolution in authoritative extract contracts."""
from __future__ import annotations

from services.planning.extract_contract_from_classification import (
    build_extract_contract_from_classification,
)
from services.planning.plan_builder import _extract_step_label
from services.planning.related_select_fields import (
    format_related_extract_hints,
    resolve_sf_related_select_paths,
    resolve_sql_related_columns_from_intent,
    resolve_sql_related_tables_from_intent,
)


def _contact_account_mi():
    return {
        "driving_entity_key": "Contact",
        "driving_entity_label": "Contact",
        "field_hints": ["FirstName", "LastName", "Email"],
        "field_hints_strict": True,
        "extract_field_scope": "explicit",
        "extract_fields_confirmed": True,
        "include_related_data": True,
        "parent_entity_key": "Account",
        "parent_entity_label": "Account",
        "related_entity_key": "Account",
        "lookup_relationship_name": "Account",
        "lookup_field_path": "Contact.AccountId",
        "related_field_hints": ["name", "phone"],
    }


def _contact_account_classification():
    mi = _contact_account_mi()
    return {
        "source": "salesforce",
        "source_name": "SF 2",
        "plan_contract": {
            "source": {
                "entity_key": "Contact",
                "entity_label": "Contact",
                "resolution_source": "llm_bind",
                "confidence": 0.95,
                "connector_type": "salesforce",
            },
            "migration_intent": mi,
        },
        "migration_intent": mi,
    }


def test_resolve_sf_related_paths_from_hints():
    paths, metadata, lookup_rels = resolve_sf_related_select_paths(_contact_account_mi())
    assert "Account.Name" in paths
    assert "Account.Phone" in paths
    assert any(m.get("api_name") == "Account.Name" for m in metadata)
    assert "Account" in lookup_rels


def test_sf_extract_contract_includes_related_paths():
    contract = build_extract_contract_from_classification(
        _contact_account_classification(),
        "salesforce",
    )
    allowed = contract.get("allowed_select_fields") or []
    assert contract.get("binding_authoritative") is True
    assert "FirstName" in allowed
    assert "Account.Name" in allowed
    assert "Account.Phone" in allowed
    assert contract.get("include_related_data") is True


def test_sf_primary_only_contract_unchanged():
    mi = {
        "driving_entity_key": "Contact",
        "field_hints": ["Email"],
        "field_hints_strict": True,
        "extract_field_scope": "explicit",
        "extract_fields_confirmed": True,
    }
    classification = {
        "plan_contract": {
            "source": {
                "entity_key": "Contact",
                "resolution_source": "llm_bind",
                "confidence": 0.9,
                "connector_type": "salesforce",
            },
            "migration_intent": mi,
        },
        "migration_intent": mi,
    }
    contract = build_extract_contract_from_classification(classification, "salesforce")
    allowed = contract.get("allowed_select_fields") or []
    assert "Email" in allowed
    assert not any("." in f for f in allowed)


def test_extract_step_label_separates_primary_and_related_hints():
    label = _extract_step_label(
        "SF 2",
        "salesforce",
        _contact_account_classification(),
    )
    assert "FirstName" in label
    assert "name" in label
    assert "phone" in label
    assert "related Account" in label


def test_format_related_extract_hints():
    text = format_related_extract_hints(
        primary_hints=["FirstName", "Email"],
        related_hints=["name", "phone"],
        field_hints_strict=True,
        include_related=True,
        parent_label="Account",
    )
    assert "FirstName" in text
    assert "related Account" in text
    assert "name" in text


def test_sql_related_tables_from_parent_entity_key():
    mi = {
        "include_related_data": True,
        "driving_entity_key": "users",
        "parent_entity_key": "user_plans",
        "join_entity_keys": ["users", "user_plans"],
        "field_hints": ["email"],
        "related_field_hints": ["plan_name"],
        "extract_field_scope": "explicit",
        "extract_fields_confirmed": True,
    }
    tables = resolve_sql_related_tables_from_intent(mi, {"source_entity_key": "users"}, primary_table="users")
    assert "users" in tables
    assert "user_plans" in tables


def test_sql_related_columns_split_by_table():
    schemas = {
        "users": {"id": "int", "email": "text", "internal_flag": "bool"},
        "user_plans": {"id": "int", "plan_name": "text", "user_id": "int"},
    }
    mi = {
        "include_related_data": True,
        "field_hints": ["email"],
        "related_field_hints": ["plan_name"],
        "extract_field_scope": "explicit",
    }
    selected, meta = resolve_sql_related_columns_from_intent(
        ["users", "user_plans"],
        mi,
        table_schemas=schemas,
    )
    assert "email" in selected
    assert "plan_name" in selected
    tables = {m["table_name"] for m in meta}
    assert "users" in tables
    assert "user_plans" in tables


def test_sql_extract_contract_includes_join_table_and_columns():
    mi = {
        "driving_entity_key": "users",
        "field_hints": ["email"],
        "field_hints_strict": True,
        "extract_field_scope": "explicit",
        "extract_fields_confirmed": True,
        "include_related_data": True,
        "parent_entity_key": "user_plans",
        "related_field_hints": ["plan_name"],
        "join_entity_keys": ["users", "user_plans"],
    }
    classification = {
        "source": "sql",
        "source_entity_key": "users",
        "related_entity_keys": ["user_plans"],
        "plan_contract": {
            "source": {
                "entity_key": "users",
                "resolution_source": "llm_bind",
                "confidence": 0.9,
                "connector_type": "sql",
            },
            "migration_intent": mi,
        },
        "migration_intent": mi,
    }
    contract = build_extract_contract_from_classification(classification, "sql")
    assert "users" in (contract.get("table_names") or [])
    assert "user_plans" in (contract.get("table_names") or [])
    assert "email" in (contract.get("allowed_select_columns") or [])
    assert "plan_name" in (contract.get("allowed_select_columns") or [])
