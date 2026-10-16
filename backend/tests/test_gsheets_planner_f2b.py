"""
Frontend→backend planner flows for SF → Google Sheets (no live LLM/DB).

Mirrors: ChatUploader → POST /architect/stream → planner_resume selections → approve_plan.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest

from services.intent.enrich import apply_post_classification_pipeline
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.orchestrator import resume_classification_after_clarifications
from services.planning.plan_approve import approve_execution_plan
from services.planning.plan_builder import build_plan_steps
from services.planning.resource_catalog import (
    GoogleSheetRef,
    ResourceCatalog,
    SalesforceConnectionRef,
)
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1", environment_type="production"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="contact list",
                sheet_name="Sheet1",
                spreadsheet_id="1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA",
            ),
            GoogleSheetRef(
                spreadsheet_name="Customer Contact Details",
                sheet_name="Sheet1",
                spreadsheet_id="abc-customer-001",
            ),
        ],
    )


def _plan_tools(plan: Dict[str, Any]) -> List[str]:
    return [
        (s.get("parameters") or {}).get("tool_id") for s in (plan.get("steps") or [])
    ]


def _template(plan: Dict[str, Any]) -> Optional[str]:
    return (plan.get("metadata") or {}).get("template")


async def _classify(
    classification: Dict[str, Any],
    prompt: str,
    *,
    history: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    cat = _catalog()

    async def _run():
        return await apply_post_classification_pipeline(
            classification,
            user_id="user-f2b",
            prompt=prompt,
            resource_catalog=cat,
            intent_state_for_classifier=None,
            conversation_history=history or [],
            google_sheet_rows=cat.google_sheet_rows,
        )

    async def _noop_dest(classification, **kwargs):
        return classification

    with patch(
        "services.planning.flow_steps.list_user_flows",
        new_callable=AsyncMock,
        return_value=[],
    ), patch(
        "services.planning.destination_object_enrichment.enrich_destination_target_from_prompt",
        side_effect=_noop_dest,
    ):
        return await _run()


def _pick_workbook_pending(out: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    clarifications = out.get("clarifications") or []
    assert any(c.get("clarification_type") == "pick_workbook" for c in clarifications)
    return {
        "original_prompt": prompt,
        "classification": dict(out),
        "execution_plan": out.get("execution_plan") or {},
        "clarifications": clarifications,
        "plan_id": out.get("plan_id"),
    }


async def _resume_workbook(
    pending: Dict[str, Any],
    option_id: str,
    *,
    text_val: Optional[str] = None,
) -> Dict[str, Any]:
    clarifications = pending.get("clarifications") or []
    cid = next(c["id"] for c in clarifications if c.get("clarification_type") == "pick_workbook")
    sel: Dict[str, Any] = {"clarification_id": cid, "option_id": option_id}
    if text_val:
        sel["new_workbook_title"] = text_val
    with patch(
        "services.planning.flow_steps.list_user_flows",
        new_callable=AsyncMock,
        return_value=[],
    ):
        return await resume_classification_after_clarifications(
            pending,
            [sel],
            user_id="user-f2b",
            prompt=pending.get("original_prompt"),
            catalog=_catalog(),
        )


pytestmark = pytest.mark.usefixtures("stub_llm_goal_parser")


def test_new_google_sheet_prompt_still_shows_workbook_picker():
    """Export to new sheet phrasing → destination clarification (workbook picker or title)."""
    prompt = "extract lead data from SF 1 org and export them to new google sheet"
    out = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
                "mode": "extract",
            },
            prompt,
        )
    )
    types = [c.get("clarification_type") for c in (out.get("clarifications") or [])]
    assert "pick_workbook" in types or "create_workbook_name" in types


def test_scenario_1_export_ambiguous_shows_workbook_picker():
    """User: deploy to google sheet (no named workbook) → pick_workbook clarification."""
    prompt = "extract contact data from SF 1 org and deploy them to google sheet"
    out = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
                "mode": "extract",
            },
            prompt,
        )
    )
    assert out.get("needs_clarification") or out.get("clarifications")
    types = [c.get("clarification_type") for c in (out.get("clarifications") or [])]
    assert "pick_workbook" in types
    plan = out.get("execution_plan") or {}
    if plan.get("steps"):
        assert "google_sheets_create_from_prompt" not in _plan_tools(plan)


def test_scenario_2_create_new_sheet_by_prompt_includes_create_step():
    """User names new sheet in prompt → PLAN_SCOPE create step or title clarification."""
    prompt = (
        "extract contacts from SF 1 and deploy to google sheets. "
        "create new google sheet and name it sales contact list"
    )
    out = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
                "planner_create_spreadsheet": True,
                "planner_suggested_spreadsheet_title": "sales contact list",
            },
            prompt,
        )
    )
    plan = out.get("execution_plan") or {}
    tools = _plan_tools(plan)
    cls = out
    assert cls.get("planner_create_spreadsheet") is True
    if out.get("needs_clarification"):
        types = [c.get("clarification_type") for c in (out.get("clarifications") or [])]
        assert "create_workbook_name" in types or "pick_extract_fields" in types
    else:
        assert _template(plan) == "extract_export_create_sheet"
        assert "google_sheets_create_from_prompt" in tools
        assert "extract_salesforce" in tools
        assert cls.get("planner_new_spreadsheet_name") == "sales contact list"


def test_scenario_3_pick_existing_workbook_no_create_step():
    """User clicks existing sheet in panel → plan must not include create (regression)."""
    prompt = "extract contact data from SF 1 org and deploy them to google sheet"
    first = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
            },
            prompt,
        )
    )
    pending = _pick_workbook_pending(first, prompt)
    opt_id = "gs_1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA_Sheet1"
    resumed = asyncio.run(_resume_workbook(pending, opt_id))
    assert resumed.get("planner_create_spreadsheet") is False
    assert resumed.get("destination_spreadsheet_id") == "1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA"
    assert resumed.get("destination_name") == "contact list"
    plan = resumed.get("execution_plan") or {}
    tools = _plan_tools(plan)
    assert "google_sheets_create_from_prompt" not in tools
    assert "extract_salesforce" in tools
    assert "map_fields" in tools
    assert "start_migration" in tools


def test_resume_after_create_new_does_not_reopen_workbook_picker():
    """Regression: gs_create_new must not re-show pick_workbook; title slot opens next."""
    from services.planning.op_requirements import (
        append_requirement_clarifications_to_plan,
        read_plan_requirements,
    )
    from services.planning.plan_scope import sync_scope_into_classification

    prompt = "extract contacts from SF 1 and export to google sheets"
    plan = ExecutionPlan(
        clarifications=[
            Clarification(
                id="c1",
                clarification_type="pick_workbook",
                prompt="Which sheet?",
                options=[
                    ClarificationOption(
                        id="gs_create_new",
                        label="Create new spreadsheet",
                        create_new=True,
                    ),
                ],
            ),
        ],
    )
    out, _ = apply_clarification_selections(
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "source_name": "SF 1",
            "intent": "extract",
        },
        plan,
        [{"clarification_id": "c1", "option_id": "gs_create_new"}],
    )
    assert out.get("planner_create_spreadsheet") is True
    goal = {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
        "needs_destination": True,
    }
    out = sync_scope_into_classification(out, goal, goal["operations"])
    reqs = read_plan_requirements(out, parsed_goal=goal)
    plan = append_requirement_clarifications_to_plan(
        ExecutionPlan(),
        out,
        reqs,
        parsed_goal=goal,
        catalog=_catalog(),
        prompt=prompt,
    )
    open_types = [
        c.clarification_type
        for c in plan.clarifications
        if c.required and not c.selected_option_id
    ]
    assert "pick_workbook" not in open_types
    assert "create_workbook_name" in open_types


def test_scenario_4_pick_create_new_from_panel_includes_create_step():
    """User clicks Create new spreadsheet → planner flags create (name may follow)."""
    prompt = "extract contacts from SF 1 and export to google sheets"
    first = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
            },
            prompt,
        )
    )
    pending = _pick_workbook_pending(first, prompt)
    resumed = asyncio.run(_resume_workbook(pending, "gs_create_new"))
    assert resumed.get("planner_create_spreadsheet") is True


def test_scenario_5_pick_existing_with_title_then_approve_matches_frontend():
    """Full chain: clarify → resume → approve (same as Run plan)."""
    prompt = "extract contact data from SF 1 org and deploy them to google sheet"
    first = asyncio.run(
        _classify(
            {
                "source": "salesforce",
                "destination": "google_sheets",
                "source_name": "SF 1",
                "intent": "extract",
            },
            prompt,
        )
    )
    pending = _pick_workbook_pending(first, prompt)
    resumed = asyncio.run(
        _resume_workbook(pending, "gs_1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA_Sheet1")
    )
    approved = approve_execution_plan(
        {
            "original_prompt": prompt,
            "classification": resumed,
            "execution_plan": resumed.get("execution_plan"),
        }
    )
    assert approved.get("plan_status") == "approved"
    tools = _plan_tools(approved.get("execution_plan") or {})
    assert "google_sheets_create_from_prompt" not in tools


def test_scenario_6_plan_runner_passes_extraction_job_to_create_step():
    """Map/migrate steps receive spreadsheet_id from classification snapshot."""
    from agent.session import AgentSession
    from services.planning.plan_runner import refresh_step_arguments

    snap = {
        "destination_spreadsheet_id": "1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA",
        "planner_destination_confirmed": True,
        "planner_create_spreadsheet": False,
        "destination_name": "contact list",
        "destination_sheet_name": "Sheet1",
    }
    session = AgentSession(
        user_id="u1",
        conversation_id="conv-1",
        mode="extract",
        prompt="test",
        classification_snapshot=snap,
        active_job_id="job-abc",
    )
    map_args = refresh_step_arguments(
        "map_fields",
        {"destination_name": "contact list"},
        session,
    )
    assert map_args.get("spreadsheet_id") == "1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA"
    assert map_args.get("job_id") == "job-abc"
