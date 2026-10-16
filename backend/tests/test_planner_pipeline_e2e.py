"""End-to-end planner pipeline tests (no live LLM/DB)."""
import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.enrich import apply_post_classification_pipeline
from services.planning.orchestrator import enrich_classification_with_planning
from services.planning.resource_catalog import (
    GoogleSheetRef,
    ResourceCatalog,
    SalesforceConnectionRef,
    SqlConnectionRef,
)


def _gs_catalog():
    return ResourceCatalog(
        has_google_sheets=True,
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1", environment_type="production"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="CRM Export", sheet_name="Sheet1"),
            GoogleSheetRef(spreadsheet_name="Employee list", sheet_name="Sheet1"),
        ],
    )


def test_post_classification_sf_export_needs_workbook_picker(stub_llm_goal_parser):
    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_name": "SF 1",
        "intent": "extract",
        "destination_name": "contact list",
        "confidence": 0.9,
    }

    async def _run():
        return await apply_post_classification_pipeline(
            classification,
            user_id="user-1",
            prompt="extract contacts and export to google sheets",
            resource_catalog=_gs_catalog(),
            intent_state_for_classifier=None,
            conversation_history=[],
            google_sheet_rows=_gs_catalog().google_sheet_rows,
        )

    async def _noop_dest(classification, **kwargs):
        return classification

    with patch(
        "services.planning.flow_steps.list_user_flows",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        side_effect=_noop_dest,
    ):
        out = asyncio.run(_run())
    assert out.get("plan_status") in (
        "awaiting_clarification",
        "draft",
        "awaiting_approval",
    )
    if out.get("plan_status") == "awaiting_approval":
        plan = out.get("execution_plan") or {}
        assert plan.get("steps")
    else:
        assert out.get("needs_clarification") or out.get("clarifications")


def test_post_classification_create_sheet_plan(monkeypatch, stub_llm_goal_parser):
    monkeypatch.setenv("AGENT_PLANNER_V2", "true")

    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_name": "SF 1",
        "source_object_api_name": "Contact",
        "intent": "extract",
        "planner_create_spreadsheet": True,
        "planner_new_spreadsheet_name": "CRM contact list",
        "planner_destination_confirmed": True,
        "planner_destination_entity_confirmed": True,
        "destination_object_api_name": "CRM contact list",
        "confidence": 0.9,
    }

    async def _run():
        return await apply_post_classification_pipeline(
            classification,
            user_id="user-1",
            prompt="extract contacts and create new sheet CRM contact list",
            resource_catalog=_gs_catalog(),
            intent_state_for_classifier=None,
            conversation_history=[],
            google_sheet_rows=[],
        )

    async def _noop_dest(classification, **kwargs):
        return classification

    with patch(
        "services.planning.flow_steps.list_user_flows",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        side_effect=_noop_dest,
    ):
        out = asyncio.run(_run())
    plan = out.get("execution_plan") or {}
    tools = [
        (s.get("parameters") or {}).get("tool_id") for s in (plan.get("steps") or [])
    ]
    assert out.get("plan_status") in (
        "awaiting_clarification",
        "awaiting_approval",
        "draft",
    ), (
        f"unexpected plan_status={out.get('plan_status')} "
        f"ops={out.get('operations')} tokens={out.get('_pipeline_tokens')}"
    )
    if out.get("plan_status") == "awaiting_approval":
        assert "extract_salesforce" in tools
        assert "google_sheets_create_from_prompt" in tools


def test_enrich_multi_source_plan(stub_llm_goal_parser):
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Production", environment_type="production"),
        ],
        sql_connections=[SqlConnectionRef(name="Warehouse DB")],
    )
    classification = {
        "source": "salesforce",
        "source_name": "Production",
        "destination": "google_sheets",
        "destination_name": "Export",
        "intent": "extract",
        "planner_destination_confirmed": True,
        "multi_source_extract": True,
        "extract_sources": [
            {"connector_type": "salesforce", "display_name": "Production"},
            {"connector_type": "sql", "display_name": "Warehouse DB"},
        ],
    }

    async def _run():
        return await enrich_classification_with_planning(
            classification,
            user_id="user-1",
            prompt="extract from Production and Warehouse DB and export to Export sheet",
            catalog=cat,
        )

    async def _noop_dest(classification, **kwargs):
        return classification

    with patch(
        "services.planning.flow_steps.list_user_flows",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        side_effect=_noop_dest,
    ):
        out = asyncio.run(_run())
    assert out.get("planner_additional_sources") or (
        (out.get("execution_plan") or {}).get("metadata", {}).get("template")
        == "multi_source_extract"
    )
