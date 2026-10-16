"""Source bind order: connector type/instance before GS workbook catalog."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.connector_llm_resolver import LlmConnectorPick
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
    SqlConnectionRef,
)
from services.planning.source_llm_resolve import resolve_source_connector_via_llm


def _multi_connector_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1", token_id="sf-token-1"),
        ],
        sql_connections=[SqlConnectionRef(name="Test DB", connector_id="sql-1")],
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="gs-conn-1", name="My Google"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="SF 1 Contact list",
                sheet_name="Sheet1",
                spreadsheet_id="sheet-contact-1",
                google_sheets_connection_id="gs-conn-1",
            ),
        ],
    )


def _classification() -> dict:
    return {
        "source": "unknown",
        "destination": "unknown",
        "operations": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_source": True,
            "needs_destination": True,
            "source_phrase": "contact information from SF 1 org",
            "destination_phrase": "Test DB",
        },
        "source_phrase": "contact information from SF 1 org",
    }


def test_unknown_source_picks_salesforce_via_connector_llm_only():
    """SF org phrase must resolve via connector LLM, not workbook entity bind at connector stage."""
    cat = _multi_connector_catalog()
    sf_pick = LlmConnectorPick("salesforce", "SF 1", "sf-token-1", 0.92)

    async def _run():
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_source_connector",
            new_callable=AsyncMock,
            return_value=sf_pick,
        ) as connector_llm:
            out = await resolve_source_connector_via_llm(
                _classification(),
                prompt="extract contact information from SF 1 org and migrate them to Test DB",
                parsed_goal=_classification()["_plan_scope"],
                catalog=cat,
                user_id="user-1",
            )
        connector_llm.assert_awaited_once()
        assert out["source"] == "salesforce"
        assert out["source_name"] == "SF 1"
        assert out.get("_source_connector_validated")

    asyncio.run(_run())


def test_gs_connector_pins_account_without_workbook_at_connector_layer():
    cat = _multi_connector_catalog()
    gs_account_pick = LlmConnectorPick("google_sheets", "My Google", "gs-conn-1", 0.88)

    async def _run():
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_source_connector",
            new_callable=AsyncMock,
            return_value=gs_account_pick,
        ):
            out = await resolve_source_connector_via_llm(
                {
                    "source": "unknown",
                    "operations": ["extract"],
                    "_plan_scope": {
                        "operations": ["extract"],
                        "needs_source": True,
                        "source_phrase": "SF 1 Contact list",
                    },
                    "source_phrase": "SF 1 Contact list",
                },
                prompt="extract from SF 1 Contact list",
                parsed_goal={
                    "operations": ["extract"],
                    "needs_source": True,
                    "source_phrase": "SF 1 Contact list",
                },
                catalog=cat,
                user_id="user-1",
            )
        assert out["source"] == "google_sheets"
        assert out["google_sheets_connection_id"] == "gs-conn-1"
        assert out.get("source_spreadsheet_id") is None
        assert out.get("_source_connector_validated")

    asyncio.run(_run())
