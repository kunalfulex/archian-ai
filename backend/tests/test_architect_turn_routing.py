"""Gate 0 + pipeline routing for architect free-text turns."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.clarification_turn import (
    classify_architect_turn,
    is_explicit_planner_ui_resume,
    try_resume_clarification_from_prompt,
)
from services.planning.plan_context import latest_clarification_pending_from_history


def _gate0_general():
    from services.intent.turn_type_routing import TurnTypeRoutingDecision

    return TurnTypeRoutingDecision(
        turn_family="general",
        plan_route="general_path",
        confidence=0.9,
        conversational_turn=True,
        decision_source="llm",
    )


def _gate0_pipeline():
    from services.intent.turn_type_routing import TurnTypeRoutingDecision

    return TurnTypeRoutingDecision(
        turn_family="pipeline",
        plan_route=None,
        confidence=0.9,
        decision_source="llm",
    )


def test_is_explicit_planner_ui_resume_selections():
    assert is_explicit_planner_ui_resume(
        {"selections": [{"clarification_id": "c1", "option_id": "x"}]}
    )
    assert is_explicit_planner_ui_resume({"approve_plan": True})
    assert not is_explicit_planner_ui_resume({"reply_text": "Hi"})
    assert not is_explicit_planner_ui_resume({"reply_text": "SF 2", "planner_pending": {}})


def test_latest_clarification_pending_from_history():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "plan_status": "awaiting_clarification",
                "planner_pending": {
                    "original_prompt": "extract contacts from sf 1",
                    "clarifications": [
                        {
                            "id": "c1",
                            "clarification_type": "pick_destination_connector",
                            "required": True,
                            "options": [],
                        }
                    ],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        }
    ]
    pending = latest_clarification_pending_from_history(history)
    assert pending is not None
    assert pending.get("original_prompt") == "extract contacts from sf 1"


def test_latest_clarification_pending_skips_resolved():
    history = [
        {
            "plannerResolved": True,
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "clarifications": [{"id": "c1", "required": True}],
                },
            },
        }
    ]
    assert latest_clarification_pending_from_history(history) is None


def test_classify_architect_turn_hi_uses_gate0_general():
    async def _run():
        with patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=_gate0_general(),
        ), patch(
            "handlers.classification_handler.classify_source_destination",
            new_callable=AsyncMock,
        ) as mock_cls:
            out = await classify_architect_turn(
                "Hi",
                "user-1",
                conversation_history=[
                    {
                        "metadata": {
                            "event_type": "planner_clarification_required",
                            "planner_pending": {
                                "clarifications": [{"id": "c1", "required": True}],
                            },
                        }
                    }
                ],
            )
            mock_cls.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out.get("plan_route") == "general_path"
    assert out.get("mode") == "general"


def test_classify_architect_turn_planning_continuation_after_gate0():
    resumed = {"needs_clarification": False, "plan_status": "awaiting_approval"}
    from services.intent.pipeline_turn_routing import PipelineTurnDecision

    planning_decision = PipelineTurnDecision(
        branch="planning_continuation",
        confidence=0.9,
        decision_source="llm",
    )

    async def _run():
        with patch(
            "services.intent.turn_type_routing.resolve_turn_type_routing",
            new_callable=AsyncMock,
            return_value=_gate0_pipeline(),
        ) as mock_gate0, patch(
            "services.intent.pipeline_turn_routing.resolve_pipeline_turn",
            new_callable=AsyncMock,
            return_value=planning_decision,
        ), patch(
            "services.intent.pipeline_turn.continue_planning_from_prompt",
            new_callable=AsyncMock,
            return_value=resumed,
        ) as mock_continue, patch(
            "services.intent.session_continuation.resolve_session_continuation",
            new_callable=AsyncMock,
        ) as mock_s01:
            out = await classify_architect_turn("SF 2", "user-1", [])
            mock_gate0.assert_called_once()
            mock_continue.assert_called_once()
            mock_s01.assert_not_called()
            return out

    out = asyncio.run(_run())
    assert out is resumed


def test_try_resume_returns_none_when_parse_fails():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "extract from sf 1",
                    "clarifications": [
                        {
                            "id": "c1",
                            "clarification_type": "pick_destination_connector",
                            "required": True,
                            "options": [],
                        }
                    ],
                },
            }
        }
    ]

    async def _run():
        with patch(
            "services.planning.clarification_reply_routing.resolve_clarification_reply_routing",
            new_callable=AsyncMock,
        ) as mock_reply, patch(
            "services.planning.binding.clarification_resolve.resolve_entity_clarification_selections",
            new_callable=AsyncMock,
            return_value=[],
        ):
            from services.planning.clarification_reply_routing import ClarificationReplyDecision

            mock_reply.return_value = ClarificationReplyDecision(
                is_clarification_reply=False,
                confidence=0.9,
                decision_source="llm",
            )
            return await try_resume_clarification_from_prompt(
                "wassup how's it going?",
                history,
                user_id="user-1",
            )

    assert asyncio.run(_run()) is None


def test_try_resume_reassembles_when_pending_has_no_open_clarifications():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "export leads to new sheet",
                    "execution_plan": {"steps": [], "status": "draft"},
                    "clarifications": [],
                    "classification": {"destination": "google_sheets"},
                },
            }
        }
    ]

    async def _run():
        with patch(
            "services.planning.orchestrator.resume_classification_after_clarifications",
            new_callable=AsyncMock,
            return_value={"plan_status": "awaiting_approval", "execution_plan": {"steps": [{}]}},
        ) as resume_mock:
            out = await try_resume_clarification_from_prompt(
                "google sheet",
                history,
                user_id="user-1",
            )
            resume_mock.assert_called_once()
            return out

    result = asyncio.run(_run())
    assert result["plan_status"] == "awaiting_approval"
