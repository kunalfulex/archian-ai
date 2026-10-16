"""SF → new Google Sheet export: account pick, title, no header clar, connector slot."""
from __future__ import annotations

import asyncio

from services.planning.clarification_apply import apply_clarification_selections
from services.planning.clarification_pipeline import PlannerSession
from services.planning.create_metadata import apply_create_metadata_to_classification
from services.planning.destination_catalog_validate import destination_connector_slot_satisfied
from services.planning.gap_detector import _build_pick_google_account_clarification
from services.planning.op_requirements import (
    CREATE_HEADERS,
    append_requirement_clarifications_to_plan,
    read_plan_requirements,
)
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.planner_session_loop import finalize_planner_session
from services.planning.resource_catalog import GoogleSheetsConnectionRef, ResourceCatalog
from services.planning.schemas import Clarification, ExecutionPlan
from tests.plan_scope_helpers import with_plan_scope


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-a", name="Work Account"),
        ],
    )


def _export_classification() -> dict:
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_object_api_name": "Lead",
            "destination": "google_sheets",
            "planner_create_spreadsheet": True,
            "_source_endpoint_clarified": True,
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
    )
    return sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "create", "map", "migrate"],
    )


def test_account_pick_marks_authoritative_for_create_export():
    cat = _catalog()
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    assert account_clar is not None
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
    )
    assert cls["destination_connector_id"] == "conn-a"
    assert cls.get("_destination_endpoint_clarified") is True


def test_create_export_account_and_title_satisfies_connector_slot():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    cls["google_sheets_connection_id"] = "conn-a"
    cls["destination_connector_id"] = "conn-a"
    cls["_destination_endpoint_clarified"] = True
    goal = dict(cls["_plan_scope"])
    goal["spreadsheet_title"] = "Lead Data Export"
    cls = apply_create_metadata_to_classification(
        cls,
        {
            **goal,
            "spreadsheet_title": "Lead Data Export",
        },
    )
    cls["planner_spreadsheet_title_confirmed"] = True
    cls["planner_new_spreadsheet_name"] = "Lead Data Export"
    assert destination_connector_slot_satisfied(cls, cat, parsed_goal=goal)


def test_stale_plan_requirements_without_extract_rereads_on_goal_ops():
    cls = _export_classification()
    cls["_plan_requirements"] = {
        "pipeline_tokens": ["create", "map", "migrate"],
        "by_step": {
            "create": [
                {
                    "slot": "destination_connector",
                    "clarification_type": "pick_destination_connector",
                    "message": "<compose>",
                    "step": "create",
                },
                {
                    "slot": "create_headers",
                    "clarification_type": "pick_create_headers",
                    "message": "<compose>",
                    "step": "create",
                },
            ],
            "map": [],
            "migrate": [],
        },
    }
    cls["_pipeline_tokens"] = ["create", "map", "migrate"]
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    slots = {s.slot for s in reqs.all_slots()}
    assert CREATE_HEADERS not in slots
    assert "extract" in reqs.pipeline_tokens


def test_append_plan_no_pick_create_headers_for_extract_export():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    cls["google_sheets_connection_id"] = "conn-a"
    cls["destination_connector_id"] = "conn-a"
    cls["_destination_endpoint_clarified"] = True
    cls["planner_spreadsheet_title_confirmed"] = True
    cls["planner_new_spreadsheet_name"] = "Lead Data Export"
    goal = dict(cls["_plan_scope"])
    reqs = read_plan_requirements(cls, parsed_goal=goal)
    plan = append_requirement_clarifications_to_plan(
        ExecutionPlan(),
        cls,
        reqs,
        parsed_goal=goal,
        catalog=cat,
    )
    assert not any(c.clarification_type == "pick_create_headers" for c in plan.clarifications)


def test_resolve_create_tokens_merges_extract_from_goal_ops():
    from services.planning.create_metadata import resolve_create_pipeline_tokens

    cls = {"_pipeline_tokens": ["create", "map", "migrate"]}
    goal = {"operations": ["extract", "create", "map", "migrate"]}
    tokens = resolve_create_pipeline_tokens(cls, goal, pipeline_tokens=["create", "map", "migrate"])
    assert "extract" in tokens


def test_apply_with_stale_string_planning_catalog_uses_passed_catalog():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = "google_sheets"  # stale serialized pin
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    assert cls["destination_connector_id"] == "conn-a"
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "create_workbook_name" in open_types
    assert "pick_create_headers" not in open_types


def test_apply_clarification_prunes_stale_connector_clar():
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    header_clar = Clarification(
        id="hdr-stale",
        clarification_type="pick_create_headers",
        prompt="Pick headers",
        options=[],
    )
    dest_clar = Clarification(
        id="dest-stale",
        clarification_type="pick_destination_connector",
        prompt="Where?",
        options=[],
    )
    plan = ExecutionPlan(
        steps=[],
        clarifications=[account_clar, dest_clar, header_clar],
    )
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_create_headers" not in open_types
    assert "pick_destination_connector" not in open_types


def test_title_resume_restores_account_slot_after_strip():
    """Title-only resume must keep account authoritative through strip_unvalidated."""
    from services.planning.destination_catalog_validate import (
        strip_unvalidated_destination_confirm_flags,
    )
    from services.planning.gap_detector import _build_pick_google_account_clarification
    from services.planning.schemas import Clarification

    cat = _catalog()
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    title_clar = Clarification(
        id="title-1",
        clarification_type="create_workbook_name",
        prompt="Name the sheet",
        options=[],
        required=True,
    )
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    plan = ExecutionPlan(steps=[], clarifications=[account_clar, title_clar])

    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    prior_cls = dict(cls)
    prior_plan = plan.model_copy(deep=True)
    prior_cls.pop("_destination_endpoint_clarified", None)
    prior_cls.pop("_destination_connector_validated", None)

    title_open = next(
        c
        for c in plan.clarifications
        if c.clarification_type == "create_workbook_name" and not c.selected_option_id
    )
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": title_open.id, "option_id": "text:Lead Data Export"}],
        catalog=cat,
    )
    from services.planning.clarification_apply import _restore_planner_endpoint_pins

    cls, plan = _restore_planner_endpoint_pins(
        cls, prior_cls, prior_plan=prior_plan, plan= plan
    )
    cls = strip_unvalidated_destination_confirm_flags(cls)
    goal = dict(cls["_plan_scope"])
    goal["spreadsheet_title"] = "Lead Data Export"
    assert destination_connector_slot_satisfied(cls, cat, parsed_goal=goal)
    assert cls.get("planner_new_spreadsheet_name") == "Lead Data Export"


def test_account_then_title_finalize_builds_plan_steps(monkeypatch):
    """Full account → title flow should reach plan_ready, not empty_plan_no_steps."""
    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.gap_detector import _build_pick_google_account_clarification
    from services.planning.planner_session_loop import (
        PlannerTurnKind,
        classify_planner_turn_outcome,
    )
    from services.planning.schemas import Clarification

    cat = _catalog()
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    title_clar = Clarification(
        id="title-1",
        clarification_type="create_workbook_name",
        prompt="Name the sheet",
        options=[],
        required=True,
    )
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    plan = ExecutionPlan(steps=[], clarifications=[account_clar, title_clar])

    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    title_open = next(
        c
        for c in plan.clarifications
        if c.clarification_type == "create_workbook_name" and not c.selected_option_id
    )
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": title_open.id, "option_id": "text:Lead Data Export"}],
        catalog=cat,
    )
    from services.planning.destination_catalog_validate import (
        strip_unvalidated_destination_confirm_flags,
    )

    cls = strip_unvalidated_destination_confirm_flags(cls)

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

    session = PlannerSession(
        classification=cls,
        plan= plan,
        parsed_goal=cls["_plan_scope"],
        prompt="pull the lead data from SF 1 org and export it to new google sheet",
    )

    async def _run():
        return await finalize_planner_session(
            session,
            user_id="user-1",
            catalog=cat,
            conversation_id="conv-1",
        )

    result = asyncio.run(_run())
    assert len(result.plan.steps) > 0
    assert classify_planner_turn_outcome(result.classification) == PlannerTurnKind.PLAN_READY


def test_finalize_composes_title_after_account_pick(monkeypatch):
    """Resume after account pick must not emit empty create_workbook_name prompt."""
    cat = _catalog()
    cls = _export_classification()
    cls["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    composed_prompt = (
        "What should we name the new Google Sheet for your Lead export from SF 1?"
    )

    async def fake_compose(session, *, user_id, conversation_id=None):
        blocking = session.plan.blocking_clarifications()
        assert blocking
        assert blocking[0].clarification_type == "create_workbook_name"
        from services.planning.schemas import ExecutionPlan

        answered = [c for c in session.plan.clarifications if c.selected_option_id]
        session.plan = session.plan.model_copy(deep=True)
        session.plan.clarifications = answered + [
            blocking[0].model_copy(update={"prompt": composed_prompt})
        ]
        return session.plan

    monkeypatch.setattr(
        "services.planning.clarification_pipeline._compose_blocking_clarification",
        fake_compose,
    )

    session = PlannerSession(
        classification=cls,
        plan=plan,
        parsed_goal=cls["_plan_scope"],
        prompt="pull the lead data from SF 1 org and export it to new google sheet",
    )

    async def _run():
        return await finalize_planner_session(
            session,
            user_id="user-1",
            catalog=cat,
            conversation_id="conv-1",
        )

    result = asyncio.run(_run())
    blocking = result.plan.blocking_clarifications()
    assert blocking
    assert blocking[0].clarification_type == "create_workbook_name"
    assert blocking[0].prompt == composed_prompt
    assert result.classification["clarifications"][0]["prompt"] == composed_prompt
