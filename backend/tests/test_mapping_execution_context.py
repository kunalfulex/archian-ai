"""Tests for mapping execution context builder."""
from __future__ import annotations

from services.mapping.brief import compose_mapping_brief
from services.mapping.context import (
    build_mapping_execution_context,
    pinned_destination_entity_key,
)
from services.mapping.validate import mapping_config_is_empty
from services.planning.schemas import PlanEntityRef


def test_pinned_destination_entity_from_plan_contract():
    snap = {
        "plan_contract": {
            "destination": {
                "connector_type": "salesforce",
                "entity_key": "Lead",
                "display_name": "SF 2",
            }
        }
    }
    assert pinned_destination_entity_key(classification_snapshot=snap, destination="salesforce") == "Lead"


def test_build_mapping_context_uses_plan_pins_not_prompt():
    snap = {
        "plan_status": "approved",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "plan_contract": {
            "source": {
                "connector_type": "salesforce",
                "entity_key": "Lead",
                "display_name": "SF 1",
            },
            "destination": {
                "connector_type": "salesforce",
                "entity_key": "Lead",
                "display_name": "SF 2",
            },
            "migration_intent": {"cross_system": True},
        },
    }
    ctx = build_mapping_execution_context(
        classification_snapshot=snap,
        step_args={"destination_entity_key": "Lead"},
        destination="salesforce",
        session_prompt="build sales lead pipeline between SF 1 org and SF 2 org",
    )
    assert ctx.has_pinned_entity
    assert ctx.mapping_target.entity_key == "Lead"
    assert ctx.approved_plan
    assert "build sales lead pipeline" not in ctx.mapping_brief
    assert "Lead" in ctx.mapping_brief
    assert "CRITICAL" in ctx.mapping_brief


def test_compose_mapping_brief_includes_endpoints():
    brief = compose_mapping_brief(
        source=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Lead",
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Lead",
        ),
        mapping_target=PlanEntityRef(
            connector_type="salesforce",
            entity_key="Lead",
        ),
    )
    assert "SF 1" in brief
    assert "SF 2" in brief
    assert "`Lead`" in brief


def test_mapping_config_is_empty():
    assert mapping_config_is_empty({})
    assert mapping_config_is_empty({"mapping": {}})
    assert not mapping_config_is_empty({"Email": {"object": "Lead", "field": "Email"}})


def test_mapping_config_is_empty_google_sheets_column_mappings():
    gs_mapping = {
        "Id": {"column": "Id", "sheet": "Sheet1"},
        "Email": {"column": "Email", "sheet": "Sheet1"},
        "FirstName": {"column": "FirstName", "sheet": "Sheet1"},
        "LastName": {"column": "LastName", "sheet": "Sheet1"},
    }
    assert not mapping_config_is_empty(gs_mapping)
    assert not mapping_config_is_empty({"mapping": gs_mapping})


def test_mapping_config_is_empty_google_sheets_split_column_entries():
    split_mapping = {
        "Name": [
            {"column": "FirstName", "sheet": "Sheet1", "is_split_field": True},
            {"column": "LastName", "sheet": "Sheet1", "is_split_field": True},
        ],
    }
    assert not mapping_config_is_empty(split_mapping)


def test_mapping_config_is_empty_rejects_column_only_whitespace():
    assert mapping_config_is_empty({"Email": {"column": "   ", "sheet": "Sheet1"}})
