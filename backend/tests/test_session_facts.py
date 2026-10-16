import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from services.intent.routing_context import _summarize_message
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.op_requirements import (
    DESTINATION_CONNECTOR,
    PlanRequirements,
    RequirementSlot,
    _slot_satisfied,
)
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan
from services.planning.session_facts import (
    apply_session_destination_pins,
    build_conversation_session_facts,
    latest_spreadsheet_creation_metadata,
)


def test_latest_spreadsheet_creation_metadata_from_history():
    history = [
        {"metadata": {"event_type": "extraction_completed", "source_name": "SF 1"}},
        {
            "metadata": {
                "event_type": "spreadsheet_creation_completed",
                "spreadsheet_name": "Contact Information",
                "spreadsheet_id": "abc123",
            }
        },
    ]
    meta = latest_spreadsheet_creation_metadata(history)
    assert meta is not None
    assert meta["spreadsheet_name"] == "Contact Information"
    assert meta["spreadsheet_id"] == "abc123"


def test_build_conversation_session_facts():
    history = [
        {
            "metadata": {
                "event_type": "spreadsheet_creation_completed",
                "spreadsheet_name": "Contact Information",
                "spreadsheet_id": "abc123",
            }
        }
    ]
    facts = build_conversation_session_facts(history)
    assert facts["last_created_spreadsheet"]["spreadsheet_name"] == "Contact Information"
    assert facts["last_created_spreadsheet"]["spreadsheet_id"] == "abc123"


def test_apply_session_destination_pins_when_phrase_matches():
    classification = {
        "destination": "google_sheets",
        "intent": "migrate_data",
        "mode": "migrate",
        "operations": ["extract", "map", "migrate"],
    }
    goal = {"destination_phrase": "Contact Information", "operations": ["extract", "map", "migrate"]}
    facts = {
        "last_created_spreadsheet": {
            "spreadsheet_name": "Contact Information",
            "spreadsheet_id": "abc123",
        }
    }
    out, goal_out = apply_session_destination_pins(classification, goal, facts)
    assert out["destination_name"] == "Contact Information"
    assert out["destination_spreadsheet_id"] == "abc123"


def test_apply_session_destination_pins_skips_generic_phrase():
    classification = {"destination": "google_sheets"}
    goal = {"destination_phrase": "google sheets"}
    facts = {
        "last_created_spreadsheet": {
            "spreadsheet_name": "Contact Information",
            "spreadsheet_id": "abc123",
        }
    }
    out, _ = apply_session_destination_pins(classification, goal, facts)
    assert not out.get("destination_name")


def test_routing_context_summarizes_spreadsheet_creation():
    summary = _summarize_message(
        {
            "metadata": {
                "event_type": "spreadsheet_creation_completed",
                "spreadsheet_name": "Contact Information",
                "spreadsheet_id": "abc123",
            }
        }
    )
    assert "Contact Information" in (summary or "")


def test_destination_connector_gsheets_requires_workbook_for_migrate():
    from services.planning.pipeline_step_resolver import PlanContext

    slot = RequirementSlot.for_slot(DESTINATION_CONNECTOR, step="migrate")
    reqs = PlanRequirements(by_step={"migrate": [slot]}, pipeline_tokens=["extract", "map", "migrate"])
    classification = {
        "destination": "google_sheets",
        "intent": "migrate_data",
        "mode": "migrate",
    }
    assert not _slot_satisfied(
        slot,
        classification=classification,
        plan_contract=None,
        parsed_goal={"operations": ["extract", "map", "migrate"]},
        pipeline_tokens=reqs.pipeline_tokens,
        plan_context=PlanContext.from_classification(classification),
    )
    resolved = {
        "destination": "google_sheets",
        "destination_name": "Contact Information",
        "destination_spreadsheet_id": "abc123",
        "intent": "migrate_data",
        "mode": "migrate",
    }
    assert _slot_satisfied(
        slot,
        classification=resolved,
        plan_contract=None,
        parsed_goal={"operations": ["extract", "map", "migrate"]},
        pipeline_tokens=reqs.pipeline_tokens,
        plan_context=PlanContext.from_classification(resolved),
    )


def test_pick_create_headers_multi_select_aggregation():
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="c-headers",
                clarification_type="pick_create_headers",
                prompt="Pick columns",
                options=[
                    ClarificationOption(id="text:First Name", label="First Name"),
                    ClarificationOption(id="text:Last Name", label="Last Name"),
                    ClarificationOption(id="text:Email", label="Email"),
                ],
            )
        ],
    )
    cls = {"destination": "google_sheets", "intent": "create"}
    selections = [
        {"clarification_id": "c-headers", "option_id": "text:First Name"},
        {"clarification_id": "c-headers", "option_id": "text:Last Name"},
        {"clarification_id": "c-headers", "option_id": "text:Email"},
    ]
    out, _ = apply_clarification_selections(cls, plan, selections)
    assert out["planner_spreadsheet_headers"] == ["First Name", "Last Name", "Email"]


def test_pick_create_headers_strips_quotes_from_chat_reply():
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="c-headers",
                clarification_type="pick_create_headers",
                prompt="Pick columns",
                options=[],
            )
        ],
    )
    cls = {"destination": "google_sheets", "intent": "create"}
    selections = [
        {
            "clarification_id": "c-headers",
            "option_id": "fields_custom",
            "text_value": "'First Name', 'Last Name', 'Email', 'Address'.",
        },
    ]
    out, _ = apply_clarification_selections(cls, plan, selections)
    assert out["planner_spreadsheet_headers"] == [
        "First Name",
        "Last Name",
        "Email",
        "Address",
    ]


def test_pick_create_headers_header_option_id_prefix():
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="c-headers",
                clarification_type="pick_create_headers",
                prompt="Pick columns",
                options=[
                    ClarificationOption(id="header:First Name", label="First Name"),
                    ClarificationOption(id="header:Email", label="Email"),
                ],
            )
        ],
    )
    cls = {"destination": "google_sheets", "intent": "create"}
    selections = [
        {"clarification_id": "c-headers", "option_id": "header:First Name"},
        {"clarification_id": "c-headers", "option_id": "header:Email"},
    ]
    out, _ = apply_clarification_selections(cls, plan, selections)
    assert out["planner_spreadsheet_headers"] == ["First Name", "Email"]


def test_enrich_destination_phrase_from_conversation_llm():
    from services.planning.session_facts import enrich_destination_phrase_from_conversation_if_needed

    payload = (
        '{"destination_phrase": "Contact Information", '
        '"references_recent_spreadsheet": true, "confidence": 0.92}'
    )

    with patch(
        "services.architect_agent.async_openai_call",
        new=AsyncMock(
            return_value=type(
                "R",
                (),
                {"choices": [type("C", (), {"message": type("M", (), {"content": payload})()})()]},
            )()
        ),
    ):
        goal = asyncio.run(
            enrich_destination_phrase_from_conversation_if_needed(
                {"operations": ["extract", "map", "migrate"]},
                {"destination": "google_sheets", "intent": "migrate_data", "mode": "migrate"},
                {
                    "last_created_spreadsheet": {
                        "spreadsheet_name": "Contact Information",
                        "spreadsheet_id": "abc123",
                    }
                },
                user_id="u1",
                prompt="migrate them to the above created sheet",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "spreadsheet_creation_completed",
                            "spreadsheet_name": "Contact Information",
                            "spreadsheet_id": "abc123",
                        }
                    }
                ],
            )
        )
    assert goal.get("destination_phrase") == "Contact Information"
