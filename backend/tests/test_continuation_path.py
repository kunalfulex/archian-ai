"""Tests for continuation vs fresh path (Phase 7)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services.intent.continuation_planning import classification_from_job_context
from services.intent.job_context import (
    JobContext,
    ResourcePin,
)
from services.intent.session_continuation import SessionContinuationDecision


def test_classification_from_job_context_remap_after_map():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        last_completed_step="map",
        mapping_ready=True,
        source=ResourcePin(connector_type="salesforce", display_name="SF 1", entity_key="Contact"),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
        ),
        destination_object_api_name="Contact",
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="migrate",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
        reasoning_short="LLM chose migrate after map",
    )
    cls = classification_from_job_context(
        decision,
        job,
        goal={"operations": ["map"]},
    )
    assert cls["_continuation_steps"] == ["map"]
    assert cls["operations"] == ["map"]
    assert cls["mode"] == "map"


def test_classification_from_job_context_migrate_only():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        last_completed_step="map",
        mapping_ready=True,
        source=ResourcePin(connector_type="sql", display_name="My SQL", entity_key="users"),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
        ),
        destination_object_api_name="Contact",
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="migrate",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
        reasoning_short="Start migration after map",
    )
    cls = classification_from_job_context(
        decision,
        job,
        goal={"operations": ["migrate"]},
    )
    assert cls["active_job_id"] == "job-1"
    assert cls["operations"] == ["migrate"]
    assert cls["_continuation_steps"] == ["migrate"]
    assert cls["source_name"] == "My SQL"
    assert cls["source_table_name"] == "users"
    contract = cls.get("plan_contract") or {}
    assert contract.get("destination", {}).get("entity_key") == "Contact"
    assert cls["extract_fields_confirmed"] is True


def test_apply_job_destination_pins_coerces_catalog_label_to_api_name():
    from services.planning.continuation_policy import apply_job_destination_pins

    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        last_completed_step="map",
        mapping_ready=True,
        source=ResourcePin(connector_type="sql", display_name="My SQL", entity_key="users"),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Salesforce Contact Data",
        ),
        destination_object_api_name="Salesforce Contact Data",
        mapping_config={
            "FirstName": {"object": "Contact", "field": "FirstName"},
            "LastName": {"object": "Contact", "field": "LastName"},
        },
    )
    cls = apply_job_destination_pins({"active_job_id": "job-1"}, job)
    assert cls["destination_object_api_name"] == "Contact"


def test_job_context_s01_progress_dict():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        last_completed_step="map",
        next_logical_step="migrate",
        mapping_ready=True,
        source=ResourcePin(connector_type="salesforce", entity_key="Contact"),
    )
    progress = job.s01_progress_dict()
    assert progress["last_completed_step"] == "map"
    assert progress["next_logical_step"] == "migrate"
    assert progress["mapping_ready"] is True
    assert progress["steps"]["map"]["status"] == "completed"


def test_route_continuation_when_job_loaded():
    async def _run():
        from services.intent.route_turn import route_planning_path
        from services.planning.resource_catalog import ResourceCatalog

        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="migrate",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        job = JobContext(
            job_id="job-1",
            pipeline_stage="mapped",
            last_completed_step="map",
            mapping_ready=True,
            source=ResourcePin(connector_type="sql", display_name="DB", entity_key="users"),
        )
        route = await route_planning_path(
            session_decision=decision,
            prompt="start migration",
            user_id="u1",
            catalog=ResourceCatalog(),
            job_context=job,
        )
        assert route == "continuation_path"

    asyncio.run(_run())


def test_route_fresh_when_session_fresh():
    async def _run():
        from services.intent.route_turn import route_planning_path
        from services.planning.resource_catalog import ResourceCatalog

        decision = SessionContinuationDecision(
            session_action="fresh_extract",
            continuation_phase="none",
            reuse_job_id=False,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        route = await route_planning_path(
            session_decision=decision,
            prompt="extract contacts again",
            user_id="u1",
            catalog=ResourceCatalog(),
        )
        assert route == "fresh_path"

    asyncio.run(_run())


def test_plan_from_job_context_builds_migrate_step():
    async def _run():
        from services.intent.continuation_planning import plan_from_job_context
        from services.planning.resource_catalog import ResourceCatalog

        job = JobContext(
            job_id="job-1",
            pipeline_stage="mapped",
            last_completed_step="map",
            mapping_ready=True,
            source=ResourcePin(
                connector_type="sql",
                display_name="My SQL",
                entity_key="users",
            ),
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="SF Org",
                entity_key="Contact",
            ),
            destination_object_api_name="Contact",
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="migrate",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            side_effect=lambda *a, **k: {"operations": ["migrate"]},
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt="start the migration",
                session_decision=decision,
                job=job,
                catalog=ResourceCatalog(),
            )
        assert out.get("plan_id")
        assert out.get("_continuation_steps") == ["migrate"]
        assert out.get("plan_route") == "continuation_path"
        plan = out.get("execution_plan") or {}
        steps = plan.get("steps") or []
        tool_ids = [
            (s.get("parameters") or {}).get("tool_id") for s in steps if isinstance(s, dict)
        ]
        assert "start_migration" in tool_ids

    asyncio.run(_run())


def test_classification_from_job_context_data_ops_infers_filter_for_empty_records():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Contact",
        ),
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="data_ops",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
        reasoning_short="Remove empty records",
    )
    goal = {"operations": [], "data_operations": ["filter"]}
    cls = classification_from_job_context(decision, job, goal=goal)
    assert cls["_continuation_steps"] == ["data_ops"]
    assert cls["operations"] == ["filter"]


def test_classification_from_job_context_data_ops_infers_transform_and_filter():
    job = JobContext(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Contact",
        ),
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="data_ops",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
        reasoning_short="Combine columns and filter emails",
    )
    goal = {"operations": [], "data_operations": ["transform", "filter"]}
    cls = classification_from_job_context(decision, job, goal=goal)
    assert cls["_continuation_steps"] == ["data_ops"]
    assert cls["operations"] == ["transform", "filter"]


def test_classification_from_job_context_data_ops_strips_stale_map_destination():
    job = JobContext(
        job_id="job-mapped",
        pipeline_stage="mapped",
        last_completed_step="map",
        mapping_ready=True,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
        ),
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            connector_id="conn-1",
            entity_key="Contact",
        ),
        destination_object_api_name="Contact",
    )
    decision = SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="data_ops",
        reuse_job_id=True,
        job_id="job-mapped",
        confidence=0.9,
        decision_source="llm",
        reasoning_short="Split name column",
    )
    cls = classification_from_job_context(
        decision,
        job,
        goal={"operations": [], "data_operations": ["transform"]},
    )
    assert cls["_continuation_steps"] == ["data_ops"]
    assert cls.get("destination_name") is None
    assert cls.get("destination_object_api_name") is None
    assert cls.get("planner_destination_confirmed") is None
    assert cls["source_name"] == "SF 1"
    assert cls["source_object_api_name"] == "Contact"


def test_plan_from_job_context_data_ops_step_includes_operations():
    async def _run():
        from services.intent.continuation_planning import plan_from_job_context
        from services.planning.resource_catalog import ResourceCatalog

        job = JobContext(
            job_id="job-1",
            pipeline_stage="extracted",
            last_completed_step="extract",
            mapping_ready=False,
            source=ResourcePin(
                connector_type="salesforce",
                display_name="SF 2",
                entity_key="Contact",
            ),
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="data_ops",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        prompt = (
            "combine first name, last name as name and remove any invalid email id records"
        )
        mock_goal = {
            "operations": [],
            "data_operations": ["transform", "filter"],
        }

        async def _mock_goal(*args, **kwargs):
            return dict(mock_goal)

        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            side_effect=_mock_goal,
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt=prompt,
                session_decision=decision,
                job=job,
                catalog=ResourceCatalog(),
            )
        plan = out.get("execution_plan") or {}
        steps = plan.get("steps") or []
        assert len(steps) == 1
        params = steps[0].get("parameters") or {}
        assert params.get("tool_id") == "run_data_ops_plan"
        assert params.get("parent_job_id") == "job-1"
        op_ids = [x["operation_id"] for x in params.get("steps") or []]
        assert op_ids == ["transform", "filter"]

    asyncio.run(_run())


def test_route_continuation_when_map_names_different_destination():
    """Map continuation with a new destination org stays on continuation_path."""
    async def _run():
        from services.intent.route_turn import route_planning_path
        from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef

        catalog = ResourceCatalog(
            salesforce_connections=[
                SalesforceConnectionRef(name="Org A", token_id="tok-a"),
                SalesforceConnectionRef(name="Org B", token_id="tok-b"),
            ]
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="map",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        job = JobContext(
            job_id="job-1",
            pipeline_stage="extracted",
            last_completed_step="extract",
            mapping_ready=False,
            source=ResourcePin(connector_type="sql", display_name="Analytics DB", entity_key="users"),
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="Org A",
            ),
        )
        route = await route_planning_path(
            session_decision=decision,
            prompt="map the data to Org B org",
            user_id="u1",
            catalog=catalog,
            job_context=job,
        )
        assert route == "continuation_path"

    asyncio.run(_run())


def test_route_fresh_when_continuation_names_different_extract_source():
    async def _run():
        from services.intent.route_turn import route_planning_path
        from services.planning.resource_catalog import ResourceCatalog, SqlConnectionRef

        catalog = ResourceCatalog(
            sql_connections=[
                SqlConnectionRef(name="Analytics DB", connector_id="sql-a"),
                SqlConnectionRef(name="Warehouse DB", connector_id="sql-b"),
            ]
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="none",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        job = JobContext(
            job_id="job-1",
            pipeline_stage="extracted",
            source=ResourcePin(connector_type="sql", display_name="Analytics DB", entity_key="users"),
        )
        route = await route_planning_path(
            session_decision=decision,
            prompt="pull contacts from Warehouse DB database instead",
            user_id="u1",
            catalog=catalog,
            job_context=job,
            pre_classification={
                "source": "sql",
                "source_name": "Warehouse DB",
                "_plan_scope": {"operations": ["extract"]},
            },
        )
        assert route == "fresh_path"

    asyncio.run(_run())


def test_plan_from_job_context_overrides_stale_job_destination_from_prompt():
    async def _run():
        from unittest.mock import AsyncMock

        from services.intent.continuation_planning import plan_from_job_context
        from services.planning.connector_llm_resolver import LlmConnectorPick
        from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef

        catalog = ResourceCatalog(
            salesforce_connections=[
                SalesforceConnectionRef(name="Org A", token_id="tok-a"),
                SalesforceConnectionRef(name="Org B", token_id="tok-b"),
            ]
        )
        job = JobContext(
            job_id="job-1",
            pipeline_stage="extracted",
            last_completed_step="extract",
            mapping_ready=False,
            source=ResourcePin(connector_type="sql", display_name="Analytics DB", entity_key="users"),
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="Org A",
            ),
        )
        decision = SessionContinuationDecision(
            session_action="continue_active_job",
            continuation_phase="map",
            reuse_job_id=True,
            job_id="job-1",
            confidence=0.9,
            decision_source="llm",
        )
        from services.planning.binding.contract import EntityCandidate, FieldCandidate

        contact = EntityCandidate(
            entity_key="Contact",
            label="Contact",
            match_score=210.0,
            fields=[FieldCandidate(key="FirstName", label="First Name")],
        )
        async def _advance(session, **kwargs):
            session.classification["destination_object_api_name"] = "Contact"
            return session

        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            new_callable=AsyncMock,
            return_value={
                "operations": ["map"],
                "destination_phrase": "Org B",
                "destination_object_api_name": "Contact",
            },
        ), patch(
            "services.planning.planning_kernel.advance_planner_pipeline",
            new_callable=AsyncMock,
            side_effect=_advance,
        ), patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new_callable=AsyncMock,
            return_value=LlmConnectorPick("salesforce", "Org B", "tok-b", 0.95),
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ), patch(
            "services.planning.binding.retrieve.retrieve_entity_shortlist",
            new_callable=AsyncMock,
            return_value=([contact], "salesforce", {}),
        ), patch(
            "services.planning.clarification_queue.build_entity_slot_clarifications",
            new_callable=AsyncMock,
            return_value=[],
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt="map to Contact object Org B org",
                session_decision=decision,
                job=job,
                catalog=catalog,
            )

        assert out["plan_route"] == "continuation_path"
        assert out["source_name"] == "Analytics DB"
        assert out["destination_name"] == "Org B"
        assert out["destination_object_api_name"] == "Contact"

    asyncio.run(_run())
