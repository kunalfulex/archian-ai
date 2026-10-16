"""Tests for connector-native labeling schema summaries and prompts."""
from __future__ import annotations

from services.labeling.prompts import (
    build_gsheets_entity_labeling_prompt,
    build_sql_entity_labeling_prompt,
    build_salesforce_entity_labeling_prompt,
)
from services.labeling.schema_summary import (
    build_sql_labeling_schema_summary,
    relationship_hints_from_sql_foreign_keys,
    relationship_hints_from_salesforce_fields,
)


def test_sql_schema_summary_includes_foreign_keys_and_suggested_hints():
    schema_data = {
        "columns": [
            {"column_name": "id", "data_type": "uuid", "is_primary_key": True},
            {"column_name": "user_id", "data_type": "uuid"},
        ],
        "foreign_keys": [
            {
                "column_name": "user_id",
                "referenced_table": "users",
                "referenced_column": "id",
            }
        ],
    }
    summary = build_sql_labeling_schema_summary(
        table_name="user_plans",
        schema_data_raw=schema_data,
        catalog_tables=["users", "user_plans", "orders"],
    )
    assert summary["table_name"] == "user_plans"
    assert len(summary["foreign_keys"]) == 1
    assert summary["suggested_relationship_hints"] == [
        {"related_entity": "users", "relationship": "lookup", "field": "user_id"}
    ]
    assert "users" in summary["catalog_tables"]


def test_sql_hints_skip_referenced_table_not_in_catalog():
    fks = [{"column_name": "user_id", "referenced_table": "users", "referenced_column": "id"}]
    hints = relationship_hints_from_sql_foreign_keys(fks, catalog_tables={"orders"})
    assert hints == []


def test_salesforce_hints_from_lookup_fields():
    fields = [
        {"field_api_name": "AccountId", "is_lookup": True, "lookup_object_api_name": "Account"},
        {"field_api_name": "Name", "is_lookup": False},
    ]
    hints = relationship_hints_from_salesforce_fields(fields)
    assert hints == [
        {"related_entity": "Account", "relationship": "lookup", "field": "AccountId"}
    ]


def test_sql_prompt_uses_users_not_salesforce_example():
    summary = build_sql_labeling_schema_summary(
        table_name="user_plans",
        schema_data_raw={
            "columns": [{"column_name": "user_id", "data_type": "uuid"}],
            "foreign_keys": [
                {"column_name": "user_id", "referenced_table": "users", "referenced_column": "id"}
            ],
        },
        catalog_tables=["users", "user_plans"],
    )
    prompt = build_sql_entity_labeling_prompt(
        entity_key="user_plans",
        entity_display_name="user_plans",
        schema_summary=summary,
    )
    assert '"field": "ParentRecordId"' not in prompt
    assert '"related_entity": "users"' in prompt
    assert "foreign_keys" in prompt
    assert "catalog_tables" in prompt


def test_gsheets_prompt_defaults_empty_relationship_hints():
    summary = {
        "entity_key": "abc::Contacts",
        "headers": [{"name": "First Name", "type": "string"}],
        "sibling_tabs": [],
        "suggested_relationship_hints": [],
    }
    prompt = build_gsheets_entity_labeling_prompt(
        entity_key="abc::Contacts",
        entity_display_name="CRM · Contacts",
        schema_summary=summary,
    )
    assert '"field": "ParentRecordId"' not in prompt
    assert '"relationship_hints": []' in prompt


def test_finalize_semantics_sql_uses_suggested_hints():
    from services.labeling.entity_labeling_service import EntityLabelingService
    from services.labeling.models import EntitySemantics

    sem = EntitySemantics.from_llm_dict(
        {
            "primary": {"category": "Plans", "description": "User plans"},
            "relationship_hints": [
                {"related_entity": "User", "relationship": "lookup", "field": "user_id"}
            ],
        }
    )
    schema_summary = {
        "catalog_tables": ["users", "user_plans"],
        "suggested_relationship_hints": [
            {"related_entity": "users", "relationship": "lookup", "field": "user_id"}
        ],
    }
    out = EntityLabelingService()._finalize_semantics(
        sem, connector_type="sql", schema_summary=schema_summary
    )
    assert out.relationship_hints == schema_summary["suggested_relationship_hints"]


def test_finalize_semantics_metadata_table_keeps_fk_hints():
    from services.labeling.entity_labeling_service import EntityLabelingService
    from services.labeling.models import EntitySemantics

    sem = EntitySemantics.from_llm_dict(
        {
            "primary": {"category": "System Catalog", "description": "Schedule rows"},
            "entity_class": "metadata",
            "relationship_hints": [],
        }
    )
    schema_summary = {
        "catalog_tables": ["users", "connector_schema_schedules"],
        "suggested_relationship_hints": [
            {
                "related_entity": "users",
                "relationship": "lookup",
                "field": "user_id",
            }
        ],
    }
    out = EntityLabelingService()._finalize_semantics(
        sem, connector_type="sql", schema_summary=schema_summary
    )
    assert out.entity_class == "metadata"
    assert out.relationship_hints == schema_summary["suggested_relationship_hints"]


def test_salesforce_prompt_includes_suggested_hints():
    summary = {
        "object_api_name": "Contact",
        "fields": [],
        "suggested_relationship_hints": [
            {"related_entity": "Account", "relationship": "lookup", "field": "AccountId"}
        ],
    }
    prompt = build_salesforce_entity_labeling_prompt(
        entity_key="Contact",
        entity_display_name="Contact",
        schema_summary=summary,
    )
    assert "AccountId" in prompt
    assert "lookup_object_api_name" in prompt
