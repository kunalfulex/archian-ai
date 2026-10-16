"""Invariant: blocking clarifications must never emit with empty user-facing copy."""
from __future__ import annotations

import asyncio
import json
import re

from agent.events import planner_clarification_required
from services.planning.clarification_compose import (
    clarification_prompt_needs_compose,
    resolve_clarification_display_message,
)
from services.planning.schemas import Clarification, ClarificationOption


def test_clarification_prompt_needs_compose_sentinel():
    assert clarification_prompt_needs_compose("") is True
    assert clarification_prompt_needs_compose("<compose>") is True
    assert clarification_prompt_needs_compose("Pick an account") is False


def test_resolve_display_message_never_generic_for_empty_title():
    clar = {
        "id": "t1",
        "clarification_type": "create_workbook_name",
        "prompt": "",
        "options": [],
        "required": True,
    }
    text = resolve_clarification_display_message(
        [clar],
        classification={"source_name": "SF 1"},
        user_prompt="export leads to new sheet",
    )
    assert text.strip()
    assert "Please choose an option" not in text


def test_planner_clarification_required_sse_has_non_empty_message():
    clar = Clarification(
        id="c1",
        clarification_type="pick_google_account",
        prompt="",
        options=[
            ClarificationOption(id="gs_account_a", label="Work Account"),
        ],
        required=True,
    )
    classification = {
        "clarifications": [clar.model_dump(mode="json")],
        "needs_clarification": True,
        "original_prompt": "export to google sheet",
        "execution_plan": {
            "steps": [],
            "clarifications": [clar.model_dump(mode="json")],
            "status": "awaiting_clarification",
        },
    }
    raw = planner_clarification_required(classification)
    match = re.search(r"data:\s*(\{.*\})", raw, re.DOTALL)
    assert match
    payload = json.loads(match.group(1))
    message = (payload.get("message") or "").strip()
    assert message
    assert "Please choose an option" not in message


def test_finalize_never_syncs_empty_blocking_prompt(monkeypatch):
    from services.planning.clarification_apply import apply_clarification_selections
    from services.planning.clarification_pipeline import PlannerSession
    from services.planning.gap_detector import _build_pick_google_account_clarification
    from services.planning.planner_session_loop import finalize_planner_session
    from services.planning.resource_catalog import GoogleSheetsConnectionRef, ResourceCatalog
    from services.planning.schemas import ExecutionPlan
    from tests.plan_scope_helpers import with_plan_scope
    from services.planning.plan_scope import sync_scope_into_classification

    cat = ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-a", name="Work Account"),
        ],
    )
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
    classification = sync_scope_into_classification(
        classification,
        classification["_plan_scope"],
        ["extract", "create", "map", "migrate"],
    )
    classification["_planning_catalog"] = cat
    account_clar = _build_pick_google_account_clarification(
        {"destination": "google_sheets", "planner_create_spreadsheet": True},
        cat,
    )
    plan = ExecutionPlan(steps=[], clarifications=[account_clar])
    cls, plan = apply_clarification_selections(
        classification,
        plan,
        [{"clarification_id": account_clar.id, "option_id": "gs_account_conn-a"}],
        catalog=cat,
    )
    composed = "Name the new Google Sheet for your Lead export from SF 1."

    async def fake_compose(session, *, user_id, conversation_id=None):
        blocking = session.plan.blocking_clarifications()
        answered = [c for c in session.plan.clarifications if c.selected_option_id]
        session.plan = session.plan.model_copy(deep=True)
        session.plan.clarifications = answered + [
            blocking[0].model_copy(update={"prompt": composed})
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
        prompt="pull lead data from SF 1 and export to new google sheet",
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
    assert blocking[0].prompt.strip()
    assert "Please choose an option" not in blocking[0].prompt
