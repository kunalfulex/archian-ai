"""Migrate-after-map continuation: job destination inherit and binding skip."""
import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.continuation_planning import plan_from_job_context
from services.intent.job_context import JobContext, ResourcePin
from services.intent.session_continuation import SessionContinuationDecision
from services.planning.connector_llm_resolver import LlmConnectorPick
from services.planning.continuation_policy import (
    apply_job_destination_pins,
    continuation_should_inherit_job_destination,
    goal_requests_new_destination_endpoint,
    should_skip_source_binding,
)
from services.planning.goal_parser import parse_goal
from services.planning.binding.bind_destination import should_bind_destination
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
    SqlConnectionRef,
)


def test_continue_migrate_skips_source_bind_despite_stale_extract_in_ops():
    cls = {
        "active_job_id": "job-1",
        "session_action": "continue_active_job",
        "plan_route": "continuation_path",
        "_continuation_steps": ["migrate"],
        "operations": ["extract", "map", "migrate"],
        "intent": "migrate_data",
        "last_completed_step": "map",
    }
    assert should_skip_source_binding(
        cls, {"operations": ["extract", "map", "migrate"]}, prompt="start the migration"
    )


def test_apply_job_destination_pins_from_job_context():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        last_completed_step="map",
        mapping_ready=True,
        source=ResourcePin(connector_type="sql", display_name="My SQL", entity_key="users"),
        destination=ResourcePin(
            connector_type="sql",
            display_name="Test DB",
            entity_key="User Plans Data",
            connector_id="sql-1",
        ),
    )
    cls = apply_job_destination_pins({"active_job_id": "job-1"}, job)
    assert cls["destination"] == "sql"
    assert cls["destination_name"] == "Test DB"
    assert cls["destination_connector_id"] == "sql-1"
    assert cls["planner_destination_confirmed"] is True


def test_migrate_inherit_ignores_spurious_plan_scope_phrase():
    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(connector_type="sql", display_name="Test DB", connector_id="sql-1"),
    )
    cat = ResourceCatalog()
    cat.sql_connections = [SqlConnectionRef(name="Test DB", connector_id="sql-1")]
    goal = {"operations": ["migrate"], "destination_phrase": "migration"}
    assert not goal_requests_new_destination_endpoint(goal, job, cont_steps=["migrate"], catalog=cat)
    assert continuation_should_inherit_job_destination(["migrate"], job, goal, catalog=cat)


def test_plan_migrate_after_map_with_noise_phrase_builds_step():
    async def _run():
        job = JobContext(
            job_id="job-1",
            pipeline_stage="mapped",
            last_completed_step="map",
            mapping_ready=True,
            source=ResourcePin(connector_type="sql", display_name="Test DB", entity_key="user_plans"),
            destination=ResourcePin(
                connector_type="sql",
                display_name="Test DB",
                entity_key="user_plans",
                connector_id="sql-1",
            ),
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="migrate",
            reuse_job_id=True,
            job_id="job-1",
            confidence=1.0,
            decision_source="llm",
        )
        goal = {"operations": ["migrate"], "needs_destination": True, "destination_phrase": "migration"}
        cat = ResourceCatalog()
        cat.sql_connections = [SqlConnectionRef(name="Test DB", connector_id="sql-1")]
        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            side_effect=lambda *a, **k: goal,
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt="start the migration",
                session_decision=decision,
                job=job,
                catalog=cat,
            )
        assert out.get("destination") == "sql"
        assert out.get("destination_name") == "Test DB"
        steps = (out.get("execution_plan") or {}).get("steps") or []
        tool_ids = [(s.get("parameters") or {}).get("tool_id") for s in steps]
        assert "start_migration" in tool_ids

    asyncio.run(_run())


def test_parse_goal_migrate_only_when_scope_says_migrate():
    cls = {
        "intent": "migrate_data",
        "mode": "migrate",
        "active_job_id": "job-1",
        "last_completed_step": "map",
        "_plan_scope": {"operations": ["migrate"]},
        "_continuation_steps": ["migrate"],
    }
    goal = parse_goal(cls, prompt="start migration")
    assert goal["operations"] == ["migrate"]


def test_plan_migrate_to_new_google_workbook_after_sf_map():
    async def _run():
        job = JobContext(
            job_id="job-1",
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
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="migrate",
            reuse_job_id=True,
            job_id="job-1",
            confidence=1.0,
            decision_source="llm",
        )
        goal = {
            "operations": ["migrate"],
            "needs_destination": True,
            "destination_phrase": "contact list workbook of xTag Account",
        }
        cat = ResourceCatalog(
            has_google_sheets=True,
            salesforce_connections=[],
            google_sheets_connections=[
                GoogleSheetsConnectionRef(connection_id="conn-xtag", name="xTag Account"),
            ],
            google_sheet_rows=[
                GoogleSheetRef(
                    spreadsheet_name="contact list",
                    sheet_name="Sheet1",
                    spreadsheet_id="sheet-cl-1",
                ),
            ],
        )
        llm_pick = LlmConnectorPick("google_sheets", "xTag Account", "conn-xtag", 0.93)
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-cl-1",
            "spreadsheet_name": "contact list",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "conn-xtag",
        }

        async def _advance(session, **kwargs):
            session.classification["destination"] = "google_sheets"
            session.classification["destination_account_name"] = "xTag Account"
            session.classification["google_sheets_connection_id"] = "conn-xtag"
            session.classification["destination_connector_id"] = "conn-xtag"
            return session

        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            side_effect=lambda *a, **k: goal,
        ), patch(
            "services.planning.planning_kernel.advance_planner_pipeline",
            new=AsyncMock(side_effect=_advance),
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ), patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ), patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=llm_pick),
        ) as mock_llm, patch(
            "services.planning.clarification_queue.build_entity_slot_clarifications",
            new=AsyncMock(return_value=[]),
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt="migrate to xTag Account, contact list workbook",
                session_decision=decision,
                job=job,
                catalog=cat,
            )
        mock_llm.assert_awaited_once()
        assert out.get("destination") == "google_sheets"
        assert out.get("destination_name") == "contact list"
        assert "map" in [str(o).lower() for o in (out.get("operations") or [])]
        assert "migrate" in [str(o).lower() for o in (out.get("operations") or [])]
        contract_dest = (out.get("plan_contract") or {}).get("destination") or {}
        assert contract_dest.get("connector_type") == "google_sheets"

    asyncio.run(_run())


def test_should_not_bind_destination_when_mapping_ready():
    cls = {
        "active_job_id": "job-1",
        "destination_object_api_name": "Contact",
        "planner_destination_entity_confirmed": True,
        "_session_progress": {"mapping_ready": True},
    }
    assert not should_bind_destination(cls, {"operations": ["migrate"]})
