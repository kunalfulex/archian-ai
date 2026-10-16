"""Google Sheets two-level destination resolution on continuation."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.job_context import JobContext, ResourcePin
from services.planning.continuation_destination import enrich_continuation_destination_connectors
from services.planning.connector_llm_resolver import (
    LlmConnectorPick,
    apply_destination_connector_pick,
)
from services.planning.connector_guards import validate_classification_resources
from services.planning.google_sheets_destination import (
    apply_google_sheets_account_pick,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)


def _gs_catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-archian-ai", name="Archian AI"),
            GoogleSheetsConnectionRef(connection_id="conn-archian-data", name="Archian Data"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="Archian Contacts",
                sheet_name="Contacts",
                spreadsheet_id="sheet-contacts-1",
            ),
            GoogleSheetRef(
                spreadsheet_name="Archian Data Export",
                sheet_name="Sheet1",
                spreadsheet_id="sheet-export-1",
            ),
        ],
    )


def _gsheets_job() -> JobContext:
    return JobContext(
        job_id="job-gs-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="google_sheets",
            display_name="Archian Data Export",
            entity_key="sheet-export-1::Sheet1",
            connector_id="conn-archian-data",
        ),
    )


def test_account_pick_does_not_set_workbook_as_destination_name():
    cat = _gs_catalog()
    out = apply_google_sheets_account_pick(
        {"destination": "unknown"},
        LlmConnectorPick("google_sheets", "Archian AI", "conn-archian-ai", 0.8),
        cat,
    )
    assert out.get("destination_name") is None
    assert out["destination_account_name"] == "Archian AI"
    assert out["google_sheets_connection_id"] == "conn-archian-ai"
    assert out.get("planner_destination_confirmed") is not True


def test_validate_preserves_workbook_name_clears_connection_name():
    cat = _gs_catalog()
    cls = {
        "destination": "google_sheets",
        "destination_name": "Archian AI",
        "destination_connector_id": "conn-archian-ai",
        "planner_destination_confirmed": True,
    }
    out = validate_classification_resources(cls, cat)
    assert out.get("destination_name") is None
    assert out["destination_account_name"] == "Archian AI"


def test_workbook_resolution_via_llm_not_keyword_path():
    async def _run():
        job = _gsheets_job()
        cls = {
            "source": "google_sheets",
            "source_name": "Archian Data Export",
            "_continuation_steps": ["map", "migrate"],
        }
        goal = {"operations": ["map", "migrate"], "destination_phrase": "Archian Contacts"}
        account_pick = LlmConnectorPick(
            "google_sheets", "Archian AI", "conn-archian-ai", 0.9
        )
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-contacts-1",
            "spreadsheet_name": "Archian Contacts",
            "sheet_name": "Contacts",
            "google_sheets_connection_id": "conn-archian-ai",
        }
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=account_pick),
        ) as mock_llm, patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ):
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="move data to Archian Contacts google sheet",
                catalog=_gs_catalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
            from services.planning.google_sheets_destination import (
                resolve_and_apply_google_sheets_workbook,
            )

            out = await resolve_and_apply_google_sheets_workbook(
                out,
                workbook_name="Archian Contacts",
                sheet_name="Contacts",
                catalog=_gs_catalog(),
                user_id="user-1",
            )
        assert out["destination_name"] == "Archian Contacts"
        assert out["destination_spreadsheet_id"] == "sheet-contacts-1"
        assert out["google_sheets_connection_id"] == "conn-archian-ai"
        assert out.get("planner_destination_confirmed") is True
        mock_llm.assert_awaited_once()

    asyncio.run(_run())


def test_workbook_resolution_after_connector_account_pick():
    async def _run():
        job = _gsheets_job()
        cls = {
            "source": "google_sheets",
            "source_name": "Archian Data Export",
            "_continuation_steps": ["map", "migrate"],
        }
        goal = {"operations": ["map", "migrate"], "destination_phrase": "Archian Contacts"}
        account_pick = LlmConnectorPick(
            "google_sheets", "Archian AI", "conn-archian-ai", 0.8
        )
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-contacts-1",
            "spreadsheet_name": "Archian Contacts",
            "sheet_name": "Contacts",
            "google_sheets_connection_id": "conn-archian-ai",
        }
        with patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ), patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=account_pick),
        ) as mock_llm:
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="migrate data to Archian Contacts google sheet",
                catalog=_gs_catalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
        assert out["destination_name"] == "Archian Contacts"
        assert out["destination_spreadsheet_id"] == "sheet-contacts-1"
        assert out["google_sheets_connection_id"] == "conn-archian-ai"
        assert out.get("planner_destination_confirmed") is True
        mock_llm.assert_awaited_once()

    asyncio.run(_run())


def test_move_to_workbook_same_as_migrate():
    """move / migrate should resolve the same GSheet workbook via unified LLM path."""
    async def _run():
        job = JobContext(
            job_id="job-sf-move",
            pipeline_stage="extracted",
            last_completed_step="extract",
            mapping_ready=False,
            source=ResourcePin(
                connector_type="salesforce",
                display_name="SF 1",
                entity_key="Contact",
                connector_id="sf-tok-1",
            ),
        )
        cat = ResourceCatalog(
            has_google_sheets=True,
            salesforce_connections=[
                SalesforceConnectionRef(name="SF 1", token_id="sf-tok-1"),
            ],
            google_sheets_connections=[
                GoogleSheetsConnectionRef(connection_id="conn-gs", name="Archian AI"),
            ],
            google_sheet_rows=[
                GoogleSheetRef(
                    spreadsheet_name="SF 1 XYZ list",
                    sheet_name="Sheet1",
                    spreadsheet_id="sheet-xyz-1",
                ),
            ],
        )
        cls = {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Contact",
            "_continuation_steps": ["map", "migrate"],
        }
        goal = {
            "operations": ["map", "migrate"],
            "destination_phrase": "SF 1 XYZ list",
        }
        llm_pick = LlmConnectorPick("google_sheets", "Archian AI", "conn-gs", 0.93)
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-xyz-1",
            "spreadsheet_name": "SF 1 XYZ list",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "conn-gs",
        }
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=llm_pick),
        ), patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ):
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="move the data to SF 1 XYZ list",
                catalog=cat,
                goal=goal,
                job=job,
                user_id="user-1",
            )
            from services.planning.google_sheets_destination import (
                resolve_and_apply_google_sheets_workbook,
            )

            out = await resolve_and_apply_google_sheets_workbook(
                out,
                workbook_name="SF 1 XYZ list",
                sheet_name="Sheet1",
                catalog=cat,
                user_id="user-1",
            )
        assert out["destination"] == "google_sheets"
        assert out["destination_name"] == "SF 1 XYZ list"
        assert out["destination_spreadsheet_id"] == "sheet-xyz-1"
        assert out["destination_sheet_name"] == "Sheet1"
        assert out.get("destination") != "salesforce"

    asyncio.run(_run())


def test_apply_destination_connector_pick_gsheets_delegates():
    cat = _gs_catalog()
    out = apply_destination_connector_pick(
        {},
        LlmConnectorPick("google_sheets", "Archian AI", "conn-archian-ai", 0.9),
        cat,
    )
    assert out["google_sheets_connection_id"] == "conn-archian-ai"
    assert out.get("destination_name") is None
