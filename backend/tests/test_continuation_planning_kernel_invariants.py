"""Phase 0 invariants for continuation destination planning kernel."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.job_context import JobContext, ResourcePin
from services.planning.continuation_destination import enrich_continuation_destination_connectors
from services.planning.connector_llm_resolver import LlmConnectorPick
from services.planning.continuation_policy import (
    apply_continuation_endpoint_pins_from_job,
    continuation_needs_destination_llm_resolution,
)
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)


def _sf_to_gsheet_catalog() -> ResourceCatalog:
    return ResourceCatalog(
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


def _sf_extract_job() -> JobContext:
    return JobContext(
        job_id="job-sf-1",
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


def test_endpoint_pins_same_as_source_does_not_confirm_before_resolver():
    job = _sf_extract_job()
    goal = {
        "operations": ["map", "migrate"],
        "destination_same_as_source": True,
        "destination_phrase": "SF 1",
    }
    cls = {"_continuation_steps": ["map", "migrate"], "source": "salesforce", "source_name": "SF 1"}
    out = apply_continuation_endpoint_pins_from_job(cls, goal, job)
    assert out.get("destination_same_as_source") is True
    assert out.get("planner_destination_confirmed") is not True
    assert out.get("_destination_endpoint_clarified") is not True


def test_same_as_source_needs_llm_resolution():
    job = _sf_extract_job()
    goal = {
        "operations": ["map", "migrate"],
        "destination_same_as_source": True,
        "destination_phrase": "SF 1",
    }
    cls = {"_continuation_steps": ["map", "migrate"]}
    assert continuation_needs_destination_llm_resolution(
        cls,
        goal,
        job,
        catalog=_sf_to_gsheet_catalog(),
        needs_map=True,
        prompt="map back to the same org",
    )


def test_same_as_source_with_sheet_phrase_needs_llm():
    job = _sf_extract_job()
    goal = {
        "operations": ["map", "migrate"],
        "destination_same_as_source": True,
        "destination_phrase": "SF 1 XYZ list",
    }
    cls = {"_continuation_steps": ["map", "migrate"]}
    assert continuation_needs_destination_llm_resolution(
        cls,
        goal,
        job,
        catalog=_sf_to_gsheet_catalog(),
        needs_map=True,
        prompt="move the data to SF 1 XYZ list",
    )


def test_same_as_source_resolved_via_catalog_llm_pick():
    async def _run():
        job = _sf_extract_job()
        cls = {
            "_continuation_steps": ["map", "migrate"],
            "source": "salesforce",
            "source_name": "SF 1",
        }
        goal = {
            "operations": ["map", "migrate"],
            "destination_same_as_source": True,
            "destination_phrase": "SF 1",
        }
        llm_pick = LlmConnectorPick("salesforce", "SF 1", "sf-tok-1", 0.95)
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=llm_pick),
        ) as mock_llm:
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="map back to the same org",
                catalog=_sf_to_gsheet_catalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
        mock_llm.assert_awaited_once()
        assert out.get("destination_same_as_source") is True
        assert out["destination"] == "salesforce"
        assert out["destination_name"] == "SF 1"

    asyncio.run(_run())


def test_destination_phrase_invokes_resolver_despite_stale_pins():
    async def _run():
        job = _sf_extract_job()
        cls = {
            "_continuation_steps": ["map", "migrate"],
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "salesforce",
            "destination_name": "SF 1",
            "destination_connector_id": "sf-tok-1",
            "planner_destination_confirmed": True,
            "_destination_endpoint_clarified": True,
            "destination_object_api_name": "Contact",
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
        ) as mock_llm, patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ):
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="move the data to SF 1 XYZ list",
                catalog=_sf_to_gsheet_catalog(),
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
                catalog=_sf_to_gsheet_catalog(),
                user_id="user-1",
            )
        mock_llm.assert_awaited_once()
        assert out["destination"] == "google_sheets"
        assert out["destination_name"] == "SF 1 XYZ list"
        assert out["destination_spreadsheet_id"] == "sheet-xyz-1"

    asyncio.run(_run())


def test_migrate_only_mapping_ready_skips_destination_resolver():
    async def _run():
        job = JobContext(
            job_id="job-mapped",
            pipeline_stage="mapped",
            last_completed_step="map",
            mapping_ready=True,
            source=ResourcePin(connector_type="sql", display_name="Test DB", entity_key="users"),
            destination=ResourcePin(
                connector_type="sql",
                display_name="Test DB",
                entity_key="user_plans",
                connector_id="sql-1",
            ),
        )
        cls = {
            "_continuation_steps": ["migrate"],
            "source": "sql",
            "source_name": "Test DB",
        }
        goal = {"operations": ["migrate"]}
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=None),
        ) as mock_llm:
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="start the migration",
                catalog=ResourceCatalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
        mock_llm.assert_not_awaited()
        assert out.get("destination_phrase") is None

    asyncio.run(_run())


def test_migrate_only_mapping_ready_new_destination_invokes_resolver():
    async def _run():
        job = JobContext(
            job_id="job-mapped-sf",
            pipeline_stage="mapped",
            last_completed_step="map",
            mapping_ready=True,
            source=ResourcePin(
                connector_type="file",
                display_name="contacts.xlsx",
                entity_key="Contact",
            ),
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="SF 1",
                entity_key="Contact",
                connector_id="sf-tok-1",
            ),
        )
        cls = {
            "_continuation_steps": ["map", "migrate"],
            "source": "file",
            "destination": "salesforce",
            "destination_name": "SF 1",
            "destination_connector_id": "sf-tok-1",
            "planner_destination_confirmed": True,
            "destination_object_api_name": "Contact",
        }
        goal = {
            "operations": ["map", "migrate"],
            "destination_phrase": "contact list workbook of xTag Account",
        }
        llm_pick = LlmConnectorPick("google_sheets", "Archian AI", "conn-gs", 0.92)
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-xyz-1",
            "spreadsheet_name": "contact list",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "conn-gs",
        }
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=llm_pick),
        ) as mock_llm, patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ):
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="migrate to Archian AI, contact list workbook",
                catalog=_sf_to_gsheet_catalog(),
                goal=goal,
                job=job,
                user_id="user-1",
            )
        mock_llm.assert_awaited_once()
        assert out["destination"] == "google_sheets"
        assert out.get("destination_name") == "contact list"

    asyncio.run(_run())


def test_resolve_continuation_destination_mode_migrate_new_phrase():
    from services.planning.continuation_policy import (
        CONTINUATION_DEST_RESOLVE,
        resolve_continuation_destination_mode,
    )

    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(connector_type="salesforce", display_name="SF 1"),
    )
    goal = {
        "operations": ["migrate"],
        "destination_phrase": "contact list workbook of xTag Account",
    }
    mode = resolve_continuation_destination_mode(["migrate"], goal, job)
    assert mode == CONTINUATION_DEST_RESOLVE


def test_ensure_map_when_destination_changes_injects_map():
    from services.planning.continuation_policy import ensure_map_when_destination_changes

    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(connector_type="salesforce", display_name="SF 1"),
    )
    goal = {
        "operations": ["migrate"],
        "destination_phrase": "contact list workbook of xTag Account",
    }
    out = ensure_map_when_destination_changes(goal, job, ["migrate"])
    assert out["operations"] == ["map", "migrate"]


def test_goal_requests_new_destination_generic_google_sheet_phrase():
    from services.planning.continuation_policy import (
        CONTINUATION_DEST_RESOLVE,
        goal_requests_new_destination_endpoint,
        resolve_continuation_destination_mode,
    )

    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(connector_type="salesforce", display_name="SF 2", entity_key="Lead"),
    )
    goal = {
        "operations": ["create", "migrate"],
        "needs_destination": True,
        "destination_phrase": "Google Sheet",
    }
    assert goal_requests_new_destination_endpoint(goal, job, cont_steps=["create", "migrate"])
    mode = resolve_continuation_destination_mode(["create", "migrate"], goal, job)
    assert mode == CONTINUATION_DEST_RESOLVE


def test_reconcile_injects_map_for_create_migrate_google_sheet():
    from services.intent.session_continuation import SessionContinuationDecision
    from services.planning.continuation_policy import reconcile_continuation_scope_after_destination

    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(connector_type="salesforce", display_name="SF 2", entity_key="Lead"),
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="migrate",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    goal = {
        "operations": ["create", "migrate"],
        "needs_destination": True,
        "destination_phrase": "google sheets",
    }
    cls = {"active_job_id": "job-1", "plan_route": "continuation_path"}
    out, scoped, steps = reconcile_continuation_scope_after_destination(
        cls, goal, job, decision
    )
    assert "map" in steps
    assert steps.index("map") < steps.index("migrate")
    assert "map" in (scoped.get("operations") or [])
