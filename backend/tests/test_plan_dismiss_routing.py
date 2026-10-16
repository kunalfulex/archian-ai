"""Plan dismiss persistence and S12 routing when gaps exist."""
from __future__ import annotations

import asyncio

from services.intent.pipeline_routing import pipeline_routing_active
from services.intent.state import IntentState
from services.intent.steps.ready import run_step_ready
from services.intent.types import RouteTarget, TurnType
from services.planning.intent_routing import classification_is_general_turn
from services.planning.plan_context import _message_is_planner_resolved


def test_plan_dismissed_message_is_planner_resolved():
    msg = {
        "type": "system",
        "metadata": {"event_type": "plan_dismissed", "plan_dismissed": True},
    }
    assert _message_is_planner_resolved(msg) is True


def test_s12_gaps_beat_conversational_general():
    state = IntentState.from_prompt(
        "can you perform the above action again",
        classification={"intent": "general", "conversational_turn": True},
    )
    state.turn_type = TurnType.GENERAL.value
    state.conversational = True
    state.gaps = [{"clarification_type": "pick_source_connector", "message": "Which source?"}]
    state.active_gap = "pick_source_connector"

    asyncio.run(
        run_step_ready(
            state,
            classification={
                "intent": "general",
                "conversational_turn": True,
                "destination": "google_sheets",
                "_plan_scope": {"operations": ["create"]},
                "needs_clarification": True,
            },
        )
    )

    assert state.route == RouteTarget.PLANNER.value
    assert state.readiness == "need_clarification"


def test_pipeline_routing_active_with_plan_scope():
    state = IntentState.from_prompt("create a sheet", classification={"intent": "general"})
    state.turn_type = TurnType.GENERAL.value
    state.conversational = True
    cls = {
        "intent": "general",
        "conversational_turn": True,
        "_plan_scope": {"operations": ["create"]},
        "plan_status": "awaiting_clarification",
    }
    assert pipeline_routing_active(state, cls) is True


def test_classification_is_general_turn_false_when_needs_clarification():
    cls = {
        "intent": "general",
        "conversational_turn": True,
        "needs_clarification": True,
        "_plan_scope": {"operations": ["create"]},
    }
    assert classification_is_general_turn(cls, "can you perform the above action again") is False


def test_classification_is_general_turn_true_for_greeting():
    cls = {"intent": "general", "conversational_turn": True}
    assert classification_is_general_turn(cls, "hello") is True
