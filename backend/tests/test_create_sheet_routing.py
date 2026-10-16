"""Create-google-sheet clarifications: header slot parsing and reply routing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.clarification_reply import parse_clarification_reply
from services.planning.clarification_reply_routing import ClarificationReplyDecision
from services.planning.op_requirements import CREATE_HEADERS, RequirementSlot


def test_create_headers_slot_uses_pick_create_headers_type():
    slot = RequirementSlot.for_slot(CREATE_HEADERS, step="create")
    assert slot.clarification_type == "pick_create_headers"


def test_parse_create_headers_reply_comma_list():
    clarifications = [
        {
            "id": "c-headers",
            "clarification_type": "pick_create_headers",
            "required": True,
            "options": [],
        }
    ]
    selections = parse_clarification_reply(
        "first name, last name, email, address and phone",
        clarifications,
    )
    assert len(selections) == 1
    assert selections[0]["clarification_id"] == "c-headers"
    assert "text_value" in selections[0]


def _create_sheet_pending_history():
    return [
        {
            "type": "user",
            "text": "can you create a google sheet for me?",
        },
        {
            "type": "assistant",
            "text": "Which column headers should the new sheet have?",
            "metadata": {
                "event_type": "planner_clarification_required",
                "plan_status": "awaiting_clarification",
                "planner_pending": {
                    "original_prompt": "can you create a google sheet for me?",
                    "clarifications": [
                        {
                            "id": "c-headers",
                            "clarification_type": "pick_create_headers",
                            "required": True,
                            "prompt": "Which column headers should the new sheet have?",
                            "options": [],
                        }
                    ],
                    "classification": {
                        "plan_status": "awaiting_clarification",
                        "intent": "create",
                    },
                },
            },
        },
    ]


def test_clarification_reply_routing_field_list_is_reply():
    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new_callable=AsyncMock,
        ) as mock_llm:
            mock_llm.return_value.choices = [
                type(
                    "C",
                    (),
                    {
                        "message": type(
                            "M",
                            (),
                            {
                                "content": (
                                    '{"is_clarification_reply": true, "confidence": 0.95,'
                                    '"reasoning_short": "column header list"}'
                                )
                            },
                        )()
                    },
                )()
            ]
            from services.planning.clarification_reply_routing import (
                resolve_clarification_reply_routing,
            )

            return await resolve_clarification_reply_routing(
                "first name, last name, email",
                user_id="u1",
                conversation_history=_create_sheet_pending_history(),
                original_prompt="can you create a google sheet for me?",
                open_clarifications=[
                    {
                        "clarification_type": "pick_create_headers",
                        "prompt": "Which column headers?",
                    }
                ],
            )

    decision = asyncio.run(_run())
    assert isinstance(decision, ClarificationReplyDecision)
    assert decision.is_clarification_reply is True
    assert decision.decision_source == "llm"
