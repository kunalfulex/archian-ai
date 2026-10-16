"""Tests for semantics-first EGA entity filters."""
from __future__ import annotations

from services.semantic_mapping.entity_filters import should_exclude_from_ega_modeling


def test_excludes_metadata_via_entity_class():
    semantics = {
        "entity_class": "metadata",
        "primary": {"category": "System Catalog"},
    }
    assert should_exclude_from_ega_modeling(
        "sql_extracted_schemas",
        connector_type="sql",
        semantics=semantics,
    )


def test_includes_business_entity():
    semantics = {
        "entity_class": "business",
        "primary": {"category": "Users"},
    }
    assert not should_exclude_from_ega_modeling(
        "users",
        connector_type="sql",
        semantics=semantics,
    )


def test_excludes_junction_tables():
    semantics = {
        "entity_class": "junction",
        "primary": {"category": "User Roles"},
    }
    assert should_exclude_from_ega_modeling(
        "user_role_assignments",
        connector_type="sql",
        semantics=semantics,
    )


def test_metadata_category_without_entity_class():
    semantics = {
        "primary": {"category": "System Catalog"},
    }
    assert should_exclude_from_ega_modeling(
        "custom_audit_log",
        connector_type="sql",
        semantics=semantics,
    )


def test_arbitrary_customer_table_not_excluded_without_semantics():
    assert not should_exclude_from_ega_modeling(
        "dbo_dim_customer",
        connector_type="sql",
        semantics=None,
    )
