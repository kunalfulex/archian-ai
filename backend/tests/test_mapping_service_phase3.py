"""Phase 3 — unified mapping service and SQL/GS plan-pinned parity."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.mapping.field_map_gs import execute_gsheets_field_mapping
from services.mapping.field_map_sql import execute_sql_field_mapping
from services.mapping.service import execute_mapping
from services.mapping_plan.models import MappingPlanRequest
from services.mapping_plan.target_resolution import MappingTargetResolution


def _sql_request(*, resolution_source: str = "plan_pinned") -> MappingPlanRequest:
    return MappingPlanRequest(
        user_id="user-1",
        destination="sql",
        destination_name="Test DB",
        flat_data={"email": "a@b.com"},
        source_document_type="Contact",
        source_document_description="Contacts",
        mapping_instruction="Map email to users table",
        destination_connector_id="sql-conn-1",
        target_table="users",
        target_resolution_source=resolution_source,
    )


def _gs_request(*, resolution_source: str = "plan_pinned") -> MappingPlanRequest:
    return MappingPlanRequest(
        user_id="user-1",
        destination="google_sheets",
        destination_name="Lead Export",
        flat_data={"Name": "Acme"},
        source_document_type="Lead",
        source_document_description="Leads",
        mapping_instruction="Map to Leads tab",
        destination_spreadsheet_id="sheet-123",
        target_sheet="Leads",
        target_resolution_source=resolution_source,
    )


def test_sql_field_mapping_passes_plan_pinned_to_handler():
    captured: dict = {}

    async def fake_map_to_database(**kwargs):
        captured.update(kwargs)
        return {
            "destination": "sql",
            "mapping": {"email": {"table": "users", "column": "email"}},
            "transformation_rules": {"fields": []},
            "primary_table": "users",
            "connector_id": "sql-conn-1",
        }

    async def _run():
        with patch("handlers.sql_handler.map_to_database", new=fake_map_to_database):
            return await execute_sql_field_mapping(_sql_request())

    result = asyncio.run(_run())
    assert captured.get("explicit_target_table") == "users"
    assert captured.get("plan_pinned_entity") is True
    assert result.destination_table == "users"
    assert result.target_resolution_source == "plan_pinned"


def test_gs_field_mapping_passes_plan_pinned_to_handler():
    captured: dict = {}

    async def fake_map_to_gsheets(**kwargs):
        captured.update(kwargs)
        return {
            "destination": "google_sheets",
            "mapping": {"Name": {"sheet": "Leads", "column": "Name"}},
            "transformation_rules": {"fields": []},
            "spreadsheet_id": "sheet-123",
        }

    async def _run():
        with patch(
            "handlers.google_sheets_handler.map_to_google_sheets",
            new=fake_map_to_gsheets,
        ):
            return await execute_gsheets_field_mapping(_gs_request())

    result = asyncio.run(_run())
    assert captured.get("destination_sheet_name") == "Leads"
    assert captured.get("plan_pinned_entity") is True
    assert result.destination_sheet == "Leads"


def test_execute_mapping_dispatches_sql_adapter():
    async def fake_sql(request, *, classification_snapshot=None):
        del classification_snapshot
        return type(
            "R",
            (),
            {
                "destination": "sql",
                "destination_name": request.destination_name,
                "destination_document_type": "Contact",
                "destination_document_description": "",
                "mapping": {"email": {"table": "users", "column": "email"}},
                "transformation_rules": {"fields": []},
                "source_document_type": request.source_document_type,
                "source_document_description": request.source_document_description,
                "destination_object_api_name": None,
                "destination_table": "users",
                "destination_sheet": None,
                "mapping_plan": None,
                "available_objects": None,
                "available_tables": None,
                "available_sheets": None,
                "connector_id": "sql-1",
                "token_id": None,
                "spreadsheet_id": None,
                "primary_table": "users",
                "org_rules_display_by_object": None,
                "target_resolution_source": "plan_pinned",
                "mapping_audit": None,
            },
        )()

    async def _run():
        with patch(
            "services.mapping.service.execute_sql_field_mapping",
            new=AsyncMock(side_effect=fake_sql),
        ):
            return await execute_mapping(
                user_id="user-1",
                destination="sql",
                destination_name="Test DB",
                flat_data={"email": "x@y.com"},
                target_resolution=MappingTargetResolution(
                    connector_type="sql",
                    resolved_target="users",
                    target_table="users",
                    resolution_source="plan_pinned",
                ),
            )

    out = asyncio.run(_run())
    assert out["destination"] == "sql"
    assert out["destination_table"] == "users"
    assert out["target_resolution_source"] == "plan_pinned"
