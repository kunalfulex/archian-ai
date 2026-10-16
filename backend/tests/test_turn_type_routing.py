"""Gate 0 LLM turn-family routing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.turn_type_routing import resolve_turn_type_routing


GOLDEN_INFO_PROMPT = (
    "where do we store customer information in salesforce sf 1 org?"
)


def test_resolve_turn_type_routing_uses_llm_when_confident():
    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value.choices = [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Msg",
                            (),
                            {"content": '{"turn_family":"info","confidence":0.92,"conversational_turn":false,"off_topic":false,"reasoning_short":"schema question"}'},
                        )()
                    },
                )()
            ]
            decision = await resolve_turn_type_routing(
                GOLDEN_INFO_PROMPT,
                user_id="user-1",
            )
            mock_llm.assert_called_once()
            return decision

    decision = asyncio.run(_run())
    assert decision.turn_family == "info"
    assert decision.plan_route == "info_path"
    assert decision.decision_source == "llm"


def test_resolve_turn_type_routing_create_sheet_pipeline():
    history = [{"type": "user", "text": "I need a new spreadsheet for leads"}]

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value.choices = [
                type(
                    "Choice",
                    (),
                    {
                        "message": type(
                            "Msg",
                            (),
                            {
                                "content": '{"turn_family":"pipeline","confidence":0.91,"conversational_turn":false,"off_topic":false,"reasoning_short":"user wants to create a sheet"}'
                            },
                        )()
                    },
                )()
            ]
            return await resolve_turn_type_routing(
                "just create the google sheet",
                user_id="user-1",
                conversation_history=history,
            )

    decision = asyncio.run(_run())
    assert decision.turn_family == "pipeline"
    assert decision.plan_route is None


def test_resolve_turn_type_routing_defaults_pipeline_on_llm_failure():
    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM down"),
        ):
            return await resolve_turn_type_routing(
                GOLDEN_INFO_PROMPT,
                user_id="user-1",
            )

    decision = asyncio.run(_run())
    assert decision.turn_family == "pipeline"
    assert decision.decision_source == "rules_fallback"
