"""Tests for step contracts (create, join, gsheets extract)."""
from __future__ import annotations

from services.planning.create_contract import build_create_spreadsheet_contract
from services.planning.deploy_contract import build_deploy_contract
from services.planning.extract_contract_from_classification import (
    build_extract_contract_from_classification,
)
from services.planning.gsheets_extract_contract import build_gsheets_extract_contract
from services.planning.join_contract import build_join_contract
from services.planning.plan_runner import _arguments_for_tool
from services.planning.search_contract import build_search_contract
from services.planning.upsert_contract import build_upsert_contract


def test_create_contract_from_classification():
    contract = build_create_spreadsheet_contract(
        {
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "CRM",
            "planner_spreadsheet_headers": ["Name", "Email"],
        },
        {"operations": ["create"], "create_destination": True},
    )
    assert contract["spreadsheet_title"] == "CRM"
    assert contract["headers"] == ["Name", "Email"]
    assert contract["create_destination"] is True


def test_join_contract_multi_source():
    contract = build_join_contract(
        {
            "source_name": "Book A",
            "source_sheet_name": "Tab1",
            "destination_name": "Book B",
            "destination_sheet_name": "Tab2",
            "join_key": "Email",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "Book B", "sheet_name": "Tab2"}
            ],
        }
    )
    assert contract["join_key"] == "Email"
    assert contract["operation"] == "join"
    assert len(contract["additional_sources"]) == 1


def test_gsheets_extract_contract_pins_sheet():
    contract = build_gsheets_extract_contract(
        {
            "source_name": "Leads",
            "source_sheet_name": "Sheet1",
            "source_spreadsheet_id": "abc123",
            "migration_intent": {"field_hints": ["Email", "Name"]},
        }
    )
    assert contract["spreadsheet_name"] == "Leads"
    assert contract["sheet_name"] == "Sheet1"
    assert contract["column_headers"] == ["Email", "Name"]


def test_plan_runner_create_includes_contract():
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="create",
        classification_snapshot={
            "planner_new_spreadsheet_name": "Export",
            "planner_spreadsheet_headers": ["A", "B"],
            "_plan_scope": {"operations": ["create"]},
        },
    )
    args = _arguments_for_tool(
        "google_sheets_create_from_prompt",
        session,
        {},
        {"spreadsheet_title": "Export"},
    )
    assert args.get("create_contract", {}).get("spreadsheet_title") == "Export"
    assert args.get("headers") == ["A", "B"]


def test_sf_extract_contract_from_classification():
    contract = build_extract_contract_from_classification(
        {
            "source_object_api_name": "Contact",
            "migration_intent": {"field_hints": ["Email", "LastName"]},
        },
        "salesforce",
    )
    assert contract["primary_object"] == "Contact"
    assert "Email" in contract["allowed_select_fields"]


def test_sql_extract_contract_from_classification():
    contract = build_extract_contract_from_classification(
        {
            "source_object_api_name": "users",
            "migration_intent": {"field_hints": ["email", "first_name"]},
        },
        "sql",
    )
    assert contract["primary_table"] == "users"
    assert "email" in contract["allowed_select_columns"]


def test_deploy_contract_pins_orgs():
    contract = build_deploy_contract(
        {
            "source": "salesforce",
            "source_name": "Production",
            "destination": "salesforce",
            "destination_name": "Sandbox",
        },
        {"operations": ["deploy_schema"]},
    )
    assert contract["source_name"] == "Production"
    assert contract["destination_name"] == "Sandbox"


def test_plan_runner_sf_extract_includes_contract():
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "source_name": "Prod",
            "source_object_api_name": "Account",
            "migration_intent": {"field_hints": ["Name"]},
        },
    )
    args = _arguments_for_tool("extract_salesforce", session, {}, {})
    assert args.get("extract_contract", {}).get("primary_object") == "Account"


def test_plan_runner_deploy_includes_contract():
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="extract",
        classification_snapshot={
            "source_name": "Production",
            "destination_name": "Sandbox",
            "_plan_scope": {"operations": ["deploy_schema"]},
        },
    )
    args = _arguments_for_tool("metadata_deploy_salesforce", session, {}, {})
    assert args.get("deploy_contract", {}).get("source_name") == "Production"


def test_plan_runner_upsert_includes_contract():
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="migrate",
        classification_snapshot={
            "destination_name": "Leads",
            "upsert_key_columns": ["Email"],
            "_plan_scope": {"operations": ["upsert"]},
        },
    )
    args = _arguments_for_tool(
        "gsheets_execute_operation",
        session,
        {"step_type": "upsert"},
        {"operation": "upsert"},
    )
    assert args.get("upsert_contract", {}).get("key_columns") == ["Email"]


def test_upsert_and_search_contracts():
    upsert = build_upsert_contract(
        {"destination_name": "Leads", "upsert_key_columns": ["Email"]},
    )
    assert upsert["operation"] == "upsert"
    assert upsert["key_columns"] == ["Email"]

    search = build_search_contract(
        {"source_name": "Leads", "search_criteria": "status=open"},
    )
    assert search["operation"] == "search"
    assert search["search_criteria"] == "status=open"
