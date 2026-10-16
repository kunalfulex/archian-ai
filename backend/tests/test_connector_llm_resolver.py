"""Tests for LLM-first destination connector resolution."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.continuation_destination import enrich_continuation_destination_connectors
from services.intent.job_context import JobContext, ResourcePin
from services.planning.connector_llm_resolver import (
    LlmConnectorPick,
    apply_destination_connector_pick,
    build_connector_catalog_for_llm,
)
from services.planning.resource_catalog import (
    ResourceCatalog,
    SalesforceConnectionRef,
    SqlConnectionRef,
)


def _mixed_catalog() -> ResourceCatalog:
    cat = ResourceCatalog()
    cat.sql_connections = [
        SqlConnectionRef(name="Test DB", connector_id="sql-test-db"),
    ]
    cat.salesforce_connections = [
        SalesforceConnectionRef(name="SF 1", token_id="sf-tok-1"),
    ]
    return cat


def _sql_job() -> JobContext:
    return JobContext(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="sql",
            display_name="Test DB",
            entity_key="user_plans",
            connector_id="sql-test-db",
        ),
    )


def test_build_connector_catalog_for_llm_includes_all_types():
    payload = build_connector_catalog_for_llm(_mixed_catalog())
    names = {p["display_name"] for p in payload}
    assert "Test DB" in names
    assert "SF 1" in names


def test_enrich_continuation_uses_llm_destination_not_label_scoring():
    async def _run():
        job = _sql_job()
        cls = {
            "source": "sql",
            "source_name": "Test DB",
            "_continuation_steps": ["map"],
        }
        goal = {
            "operations": ["map"],
            "needs_destination": True,
            "destination_phrase": "Test DB",
        }
        pick = LlmConnectorPick(
            connector_type="sql",
            display_name="Test DB",
            connector_id="sql-test-db",
            confidence=0.95,
        )
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=pick),
        ) as mock_resolve:
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="can you map the data to Test DB",
                catalog=_mixed_catalog(),
                goal=goal,
                job=job,
                user_id="u1",
            )
        mock_resolve.assert_awaited_once()
        assert out["destination"] == "sql"
        assert out["destination_name"] == "Test DB"
        assert out["destination_connector_id"] == "sql-test-db"
        assert out.get("_destination_connector_resolution") == "llm"

    asyncio.run(_run())


def test_apply_destination_connector_pick_sets_pins():
    out = apply_destination_connector_pick(
        {"destination": "unknown"},
        LlmConnectorPick("sql", "Test DB", "sql-test-db", 0.9),
    )
    assert out["destination"] == "sql"
    assert out["destination_name"] == "Test DB"
    assert out["destination_connector_id"] == "sql-test-db"
    assert out.get("_destination_connector_resolution") == "llm"
    assert out.get("_destination_endpoint_clarified") is None


def test_build_workbook_catalog_for_llm_lists_spreadsheet_names():
    from services.planning.connector_llm_resolver import build_workbook_catalog_for_llm
    from services.planning.resource_catalog import GoogleSheetRef

    cat = _mixed_catalog()
    cat.google_sheet_rows = [
        GoogleSheetRef(
            spreadsheet_name="SF 1 XYZ list",
            sheet_name="Sheet1",
            spreadsheet_id="sheet-xyz-1",
        ),
    ]
    workbooks = build_workbook_catalog_for_llm(cat)
    assert any(w["workbook_name"] == "SF 1 XYZ list" for w in workbooks)
    assert workbooks[0]["sheet_tabs"] == ["Sheet1"]
