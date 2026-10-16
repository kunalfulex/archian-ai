"""Planner session loop — outcome contract and finalize invariants."""
from __future__ import annotations

import asyncio

from services.planning.planner_session_loop import (
    PlannerTurnKind,
    build_planner_turn_outcome,
    classify_planner_turn_outcome,
    sync_classification_from_plan,
)
from services.planning.schemas import Clarification, ExecutionPlan


def test_classify_outcome_clarification():
    cls = {"needs_clarification": True, "plan_status": "awaiting_clarification"}
    assert classify_planner_turn_outcome(cls) == PlannerTurnKind.CLARIFICATION


def test_classify_outcome_plan_ready_with_steps():
    cls = {
        "needs_clarification": False,
        "plan_status": "awaiting_approval",
        "execution_plan": {"steps": [{"step_type": "extract", "parameters": {}}]},
    }
    assert classify_planner_turn_outcome(cls) == PlannerTurnKind.PLAN_READY


def test_classify_outcome_error_zero_steps():
    cls = {
        "needs_clarification": False,
        "plan_status": "draft",
        "execution_plan": {"steps": []},
    }
    assert classify_planner_turn_outcome(cls) == PlannerTurnKind.ERROR


def test_sync_classification_from_plan_clarification():
    plan = ExecutionPlan(
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c1",
                clarification_type="pick_google_account",
                prompt="Pick account",
                options=[],
            )
        ],
    )
    out = sync_classification_from_plan({}, plan)
    assert out["needs_clarification"] is True
    assert out["plan_status"] == "awaiting_clarification"
    assert len(out["clarifications"]) == 1


def test_sync_classification_from_plan_with_steps():
    plan = ExecutionPlan(
        status="awaiting_approval",
        steps=[
            {
                "step_id": "s1",
                "step_type": "extract",
                "label": "Extract",
                "parameters": {"tool_id": "extract_salesforce"},
            }
        ],
    )
    out = sync_classification_from_plan({}, plan)
    assert out["needs_clarification"] is False
    assert out["plan_status"] == "awaiting_approval"


def test_build_planner_turn_outcome_error_message():
    cls = {
        "needs_clarification": False,
        "execution_plan": {"steps": []},
    }
    outcome = build_planner_turn_outcome(cls)
    assert outcome.kind == PlannerTurnKind.ERROR
    assert outcome.message


def test_finalize_planner_session_builds_steps_when_slots_satisfied(monkeypatch):
    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.planner_session_loop import finalize_planner_session
    from services.planning.resource_catalog import ResourceCatalog
    from tests.plan_scope_helpers import with_plan_scope

    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "destination_connector_id": "acc-1",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "planner_new_spreadsheet_name": "Lead Data Export",
            "planner_destination_confirmed": True,
            "intent": "migrate_data",
            "mode": "extract",
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
        spreadsheet_title="Lead Data Export",
    )
    plan = ExecutionPlan(status="draft", goal_summary="export leads")
    session = PlannerSession(
        classification=classification,
        plan=plan,
        parsed_goal=classification["_plan_scope"],
        prompt="pull lead data from SF 1 and export to new google sheet",
    )
    catalog = ResourceCatalog(has_google_sheets=True)

    async def fake_assembly_tail(sess, **kwargs):
        from services.planning.plan_builder import build_plan_steps, finalize_plan_status

        sess.plan = build_plan_steps(
            sess.classification,
            prompt=sess.prompt,
            plan=sess.plan,
        )
        sess.plan = finalize_plan_status(sess.plan)
        return sess

    async def noop_gaps(*args, **kwargs):
        return False

    monkeypatch.setattr(
        "services.planning.clarification_pipeline._run_plan_assembly_tail",
        fake_assembly_tail,
    )
    monkeypatch.setattr(
        "services.planning.planner_session_loop._sync_requirement_gaps",
        noop_gaps,
    )

    async def _run():
        return await finalize_planner_session(
            session,
            user_id="user-1",
            catalog=catalog,
            conversation_id="conv-1",
        )

    result = asyncio.run(_run())
    assert len(result.plan.steps) > 0
    assert result.classification.get("plan_status") == "awaiting_approval"
    assert classify_planner_turn_outcome(result.classification) == PlannerTurnKind.PLAN_READY


def test_finalize_planner_session_emits_extract_fields_clarification(monkeypatch):
    """Regression: merge must not wipe pipeline pick_extract_fields → outcome error."""
    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
    from services.planning.planner_session_loop import finalize_planner_session
    from services.planning.resource_catalog import ResourceCatalog
    from services.planning.schemas import Clarification
    from tests.plan_scope_helpers import with_plan_scope

    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="Lead",
        driving_entity_label="Lead",
        extract_field_scope="vague",
        suggested_field_hints=["Name", "Company", "Email"],
        needs_extract_clarification=True,
        extract_fields_confirmed=False,
        extract_ambiguities=[
            ExtractAmbiguity(
                ambiguity_type="pick_extract_fields",
                prompt="Which Lead fields should we extract?",
            )
        ],
    )
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "source_connector_id": "sf-1",
            "_source_endpoint_clarified": True,
            "destination": "salesforce",
            "destination_name": "SF 2",
            "destination_object_api_name": "Lead",
            "destination_connector_id": "sf-2",
            "planner_destination_confirmed": True,
            "planner_destination_entity_confirmed": True,
        },
        operations=["extract", "migrate"],
    )
    plan = ExecutionPlan(
        status="awaiting_clarification",
        metadata={"migration_intent": intent.model_dump(mode="json")},
        clarifications=[
            Clarification(
                id="field-clar",
                clarification_type="pick_extract_fields",
                prompt="Which Lead fields should we extract?",
                options=[],
                required=True,
            )
        ],
    )
    session = PlannerSession(
        classification=classification,
        plan=plan,
        parsed_goal=classification["_plan_scope"],
        migration_intent=intent,
        prompt="build sales lead pipeline SF 1 to SF 2",
    )
    catalog = ResourceCatalog()

    async def noop_assembly(sess, **kwargs):
        return sess

    async def noop_gaps(*args, **kwargs):
        return False

    monkeypatch.setattr(
        "services.planning.clarification_pipeline._run_plan_assembly_tail",
        noop_assembly,
    )
    monkeypatch.setattr(
        "services.planning.planner_session_loop._sync_requirement_gaps",
        noop_gaps,
    )
    async def noop_compose(sess, **kwargs):
        return sess

    monkeypatch.setattr(
        "services.planning.planner_session_loop._ensure_blocking_clarification_composed",
        noop_compose,
    )

    async def _run():
        return await finalize_planner_session(
            session,
            user_id="user-1",
            catalog=catalog,
            conversation_id="conv-1",
        )

    result = asyncio.run(_run())
    assert not result.plan.steps
    blocking = result.plan.blocking_clarifications()
    assert blocking
    assert blocking[0].clarification_type == "pick_extract_fields"
    assert result.classification.get("needs_clarification") is True
    assert classify_planner_turn_outcome(result.classification) == PlannerTurnKind.CLARIFICATION
