"""Tests for connector info / schema query routing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from agent.planner import Planner
from agent.session import AgentSession
from services.planning.connector_llm_resolver import LlmInfoPick
from services.planning.resource_catalog import (
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)


def test_planner_routes_salesforce_info_not_general():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        prompt="What Salesforce objects are available in SF 1?",
        classification_snapshot={
            "intent": "info",
            "mode": "general",
            "source": "salesforce",
            "source_name": "SF 1",
        },
    )
    plan = asyncio.run(Planner().build_plan(session.prompt, session))
    assert plan.mode == "general"
    assert plan.steps[0].tool_id == "salesforce_info_request"
    assert plan.steps[0].arguments.get("route_source_name") == "SF 1"


def test_planner_info_without_connector_falls_back_to_sf_tool():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        prompt="What Salesforce objects do I have?",
        classification_snapshot={
            "intent": "info",
            "mode": "general",
            "source": "salesforce",
            "source_name": None,
        },
    )
    plan = asyncio.run(Planner().build_plan(session.prompt, session))
    assert plan.steps[0].tool_id == "salesforce_info_request"


def test_planner_routes_google_sheets_info_with_workbook_name():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        prompt="How many rows are in the Leads sheet?",
        classification_snapshot={
            "intent": "info",
            "mode": "general",
            "plan_route": "info_path",
            "source": "google_sheets",
            "source_name": "Leads",
        },
    )
    plan = asyncio.run(Planner().build_plan(session.prompt, session))
    assert plan.steps[0].tool_id == "google_sheets_info"
    assert plan.steps[0].arguments.get("workbook_name") == "Leads"


def test_orchestrator_enriches_google_sheets_info_without_execution_plan():
    from services.planning.orchestrator import enrich_classification_with_planning

    catalog = ResourceCatalog()
    catalog.google_sheets_connections.append(
        GoogleSheetsConnectionRef(connection_id="gs-1", name="Leads")
    )

    async def _run():
        return await enrich_classification_with_planning(
            {
                "intent": "info",
                "mode": "general",
                "plan_route": "info_path",
                "source": "google_sheets",
                "source_name": "Leads",
            },
            user_id="u",
            prompt="How many rows are in the Leads sheet?",
            catalog=catalog,
        )

    out = asyncio.run(_run())
    assert out.get("intent") == "info"
    assert out.get("source") == "google_sheets"
    assert out.get("execution_plan") is None


def test_orchestrator_binds_info_connector_via_llm():
    from services.planning.orchestrator import enrich_classification_with_planning

    catalog = ResourceCatalog()
    catalog.salesforce_connections.append(
        SalesforceConnectionRef(name="SF 1", token_id="tok-1")
    )

    async def _run():
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_info_connector",
            new_callable=AsyncMock,
            return_value=LlmInfoPick(
                connector_type="salesforce",
                display_name="SF 1",
                connector_id="tok-1",
                confidence=0.95,
            ),
        ):
            return await enrich_classification_with_planning(
                {
                    "intent": "info",
                    "mode": "general",
                    "plan_route": "info_path",
                    "source": "unknown",
                },
                user_id="u",
                prompt="What Salesforce objects are available in SF 1?",
                catalog=catalog,
            )

    out = asyncio.run(_run())
    assert out.get("source") == "salesforce"
    assert out.get("source_name") == "SF 1"
    assert out.get("_info_connector_resolution") == "llm"
