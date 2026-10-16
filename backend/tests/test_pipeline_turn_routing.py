"""Tests for unified pipeline turn routing (Phase 2)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from services.intent.pipeline_turn_routing import (
    PipelineTurnRoutingError,
    gather_pipeline_turn_context,
    resolve_pipeline_turn,
)


def _mock_llm_response(branch: str, *, confidence: float = 0.95, reasoning: str = "test"):
    mock_response = AsyncMock()
    mock_response.choices = [
        type(
            "Choice",
            (),
            {
                "message": type(
                    "Msg",
                    (),
                    {
                        "content": json.dumps(
                            {
                                "branch": branch,
                                "confidence": confidence,
                                "reasoning_short": reasoning,
                            }
                        )
                    },
                )()
            },
        )()
    ]
    return mock_response


def test_resolve_pipeline_turn_no_context_is_fresh():
    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("fresh", reasoning="new pipeline goal"),
        ):
            return await resolve_pipeline_turn(
                "migrate SF to Test DB",
                user_id="u1",
                conversation_history=[],
            )

    decision = asyncio.run(_run())
    assert decision.branch == "fresh"
    assert decision.decision_source == "llm"


def test_resolve_pipeline_turn_llm_fresh_start_supersedes_open_clarification():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "clarifications": [{"id": "c1", "required": True}],
                },
            }
        }
    ]

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=_mock_llm_response("fresh", reasoning="User asked to start over"),
        ):
            return await resolve_pipeline_turn(
                "start over with google sheets",
                user_id="u1",
                conversation_history=history,
            )

    decision = asyncio.run(_run())
    assert decision.branch == "fresh"
    assert decision.decision_source == "llm"


def test_resolve_pipeline_turn_stalled_pending_is_planning_continuation():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "export leads",
                    "clarifications": [],
                    "execution_plan": {"steps": []},
                },
            }
        }
    ]

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=_mock_llm_response(
                "planning_continuation",
                reasoning="resume stalled plan thread",
            ),
        ):
            return await resolve_pipeline_turn(
                "google sheet",
                user_id="u1",
                conversation_history=history,
            )

    decision = asyncio.run(_run())
    assert decision.branch == "planning_continuation"
    assert decision.decision_source == "llm"


def test_gather_pipeline_turn_context_includes_stalled_state():
    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "export leads",
                    "clarifications": [],
                },
            }
        }
    ]
    ctx = gather_pipeline_turn_context("google sheet", history)
    assert ctx.stalled_planner_pending is True
    assert ctx.open_clarifications == []


def test_resolve_pipeline_turn_post_migrate_create_headers_not_no_context():
    """After prior migration, open pick_create_headers must be visible to pipeline turn."""
    history = [
        {"metadata": {"event_type": "migration_completed", "status": "completed"}},
        {"type": "user", "text": "create new workbook for me"},
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "plan_status": "awaiting_clarification",
                "planner_pending": {
                    "original_prompt": "create new workbook for me",
                    "clarifications": [
                        {
                            "id": "c-headers",
                            "clarification_type": "pick_create_headers",
                            "required": True,
                            "prompt": "Which column headers?",
                        }
                    ],
                    "classification": {"plan_status": "awaiting_clarification"},
                },
            }
        },
    ]

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=_mock_llm_response(
                "planning_continuation",
                reasoning="column header list",
            ),
        ):
            return await resolve_pipeline_turn(
                "full name, email, cellphone, address and fax",
                user_id="u1",
                conversation_history=history,
            )

    decision = asyncio.run(_run())
    assert decision.branch == "planning_continuation"
    assert decision.decision_source == "llm"


def test_resolve_pipeline_turn_llm_failure_raises():
    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            side_effect=RuntimeError("api down"),
        ):
            return await resolve_pipeline_turn("migrate", user_id="u1", conversation_history=[])

    with pytest.raises(PipelineTurnRoutingError):
        asyncio.run(_run())


def test_merge_fresh_supersede_metadata_on_classifier_path():
    from services.intent.pipeline_turn import (
        merge_fresh_supersede_into_classification,
        supersede_pending_for_fresh_turn,
    )

    history = [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "create google sheet",
                    "clarifications": [{"id": "c1", "required": True}],
                },
            }
        }
    ]
    supersede = supersede_pending_for_fresh_turn(history)
    out = merge_fresh_supersede_into_classification({"intent": "migrate"}, supersede)
    assert out.get("_fresh_supersedes_pending") is True
    assert out.get("planner_pending_superseded", {}).get("superseded") is True
