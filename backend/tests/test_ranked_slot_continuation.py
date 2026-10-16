"""Ranked-slot clarifications and fresh supersede guards."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

from services.intent.pipeline_turn import supersede_pending_for_fresh_turn
from services.intent.pipeline_turn_routing import resolve_pipeline_turn


def _history_with_open_extract_fields() -> list:
    return [
        {
            "metadata": {
                "event_type": "planner_clarification_required",
                "planner_pending": {
                    "original_prompt": "migrate leads to salesforce",
                    "clarifications": [
                        {
                            "id": "c-fields",
                            "clarification_type": "pick_extract_fields",
                            "required": True,
                            "prompt": "Which columns?",
                        }
                    ],
                },
            }
        }
    ]


def test_resolve_pipeline_turn_field_list_is_planning_continuation():
    async def _run():
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
                                    "branch": "planning_continuation",
                                    "confidence": 0.95,
                                    "reasoning_short": "field list answers open gap",
                                }
                            )
                        },
                    )()
                },
            )()
        ]
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            return await resolve_pipeline_turn(
                "email, first_name",
                user_id="u1",
                conversation_history=_history_with_open_extract_fields(),
            )

    decision = asyncio.run(_run())
    assert decision.branch == "planning_continuation"
    assert decision.decision_source == "llm"


def test_resolve_pipeline_turn_respects_llm_fresh_on_ranked_slot():
    async def _run():
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
                                    "branch": "fresh",
                                    "confidence": 0.95,
                                    "reasoning_short": "new goal",
                                }
                            )
                        },
                    )()
                },
            )()
        ]
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            return_value=mock_response,
        ):
            return await resolve_pipeline_turn(
                "extract contacts from hubspot",
                user_id="u1",
                conversation_history=_history_with_open_extract_fields(),
            )

    decision = asyncio.run(_run())
    assert decision.branch == "fresh"
    assert decision.decision_source == "llm"


def test_supersede_pending_skipped_for_pick_extract_fields():
    supersede = supersede_pending_for_fresh_turn(_history_with_open_extract_fields())
    assert supersede is None
