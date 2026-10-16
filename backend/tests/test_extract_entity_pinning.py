"""Tests for plan-contract entity pinning at extract time (SQL + GSheets)."""
from __future__ import annotations

import asyncio


def test_select_relevant_tables_skips_llm_when_table_hint_valid():
    from handlers.sql_handler import select_relevant_tables

    connector_data = {
        "tables": [
            {"table_name": "customers", "ai_categorization": "{}"},
            {"table_name": "orders", "ai_categorization": "{}"},
        ]
    }

    async def _run():
        return await select_relevant_tables(
            "deploy customer data",
            connector_data,
            [],
            table_hint="customers",
        )

    assert asyncio.run(_run()) == ["customers"]


def test_select_relevant_tables_pins_join_partner_from_plan():
    from handlers.sql_handler import select_relevant_tables

    connector_data = {
        "tables": [
            {"table_name": "users", "schema_data": "{}"},
            {"table_name": "user_plans", "schema_data": "{}"},
        ]
    }

    async def _run():
        return await select_relevant_tables(
            "user details with plan details",
            connector_data,
            [],
            table_hint="users",
            related_entity_keys=["user_plans"],
            migration_intent={
                "include_related_data": True,
                "driving_entity_key": "user_plans",
                "parent_entity_key": "users",
            },
        )

    assert asyncio.run(_run()) == ["users", "user_plans"]


def test_select_relevant_tables_ignores_invalid_table_hint(monkeypatch):
    from handlers.sql_handler import select_relevant_tables

    llm_called = {"value": False}

    async def _mock_llm(*args, **kwargs):
        llm_called["value"] = True
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='["customers"]'))]
        )

    monkeypatch.setattr("services.architect_agent.async_openai_call", _mock_llm)

    connector_data = {
        "tables": [
            {"table_name": "customers", "ai_categorization": "{}"},
        ]
    }

    async def _run():
        return await select_relevant_tables(
            "get data",
            connector_data,
            [],
            table_hint="missing_table",
        )

    selected = asyncio.run(_run())
    assert llm_called["value"] is True
    assert selected == ["customers"]


def test_resolve_gsheet_from_plan_entity_key_found(monkeypatch):
    from handlers.google_sheets.detection import resolve_gsheet_from_plan_entity_key

    async def _fake_fetch_one(query, params):
        return {
            "spreadsheet_id": params["spreadsheet_id"],
            "spreadsheet_name": "Appointments Workbook",
            "sheet_name": params["sheet_name"],
        }

    monkeypatch.setattr("db.connection.database.fetch_one", _fake_fetch_one)

    async def _run():
        return await resolve_gsheet_from_plan_entity_key(
            "user-1",
            "abc123::Sheet1",
        )

    result = asyncio.run(_run())
    assert result["found"] is True
    assert result["spreadsheet_id"] == "abc123"
    assert result["sheet_name"] == "Sheet1"
    assert result["spreadsheet_name"] == "Appointments Workbook"


def test_snap_hint_prefers_plan_contract_destination():
    from services.mapping_plan.target_resolution import _snap_hint

    snap = {
        "plan_contract": {
            "destination": {
                "entity_key": "ServiceAppointment",
                "connector_type": "salesforce",
            }
        },
        "destination_object_api_name": "Contact",
    }
    assert _snap_hint(snap, "salesforce") == "ServiceAppointment"
