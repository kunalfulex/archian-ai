"""Tests for small LLM entity clarification resolution."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.planning.binding.clarification_resolve import (
    resolve_entity_clarification_llm,
    resolve_entity_clarification_selections,
)
from services.planning.binding.contract import EntityCandidate, FieldCandidate
from services.planning.clarification_reply import parse_clarification_reply


def _llm_response(payload: str) -> MagicMock:
    msg = MagicMock()
    msg.content = payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_parse_clarification_reply_no_raw_entity_passthrough():
    clar = [
        {
            "id": "c-entity",
            "clarification_type": "pick_source_entity",
            "required": True,
            "options": [],
        }
    ]
    assert parse_clarification_reply("use contact object", clar) == []


def test_resolve_entity_clarification_llm_maps_reply():
    candidates = [
        EntityCandidate(
            entity_key="Contact",
            label="Contact",
            fields=[FieldCandidate(key="FirstName", label="First Name")],
        )
    ]

    with patch(
        "services.planning.binding.clarification_resolve.retrieve_entity_shortlist",
        new=AsyncMock(return_value=(candidates, "salesforce", {})),
    ), patch(
        "services.architect_agent.async_openai_call",
        new=AsyncMock(
            return_value=_llm_response('{"entity_key": "Contact", "confidence": 0.92}')
        ),
    ):
        key = asyncio.run(
            resolve_entity_clarification_llm(
                "use contact object",
                role="source",
                user_id="u1",
                classification={"source": "salesforce", "source_name": "SF 2"},
                prompt="extract contacts from SF 2",
            )
        )
    assert key == "Contact"


def test_resolve_entity_clarification_selections_builds_option_id():
    clar = [
        {
            "id": "c-entity",
            "clarification_type": "pick_source_entity",
            "required": True,
            "options": [],
        }
    ]

    with patch(
        "services.planning.binding.clarification_resolve.resolve_entity_clarification_llm",
        new=AsyncMock(return_value="Contact"),
    ):
        selections = asyncio.run(
            resolve_entity_clarification_selections(
                "use contact object",
                clar,
                user_id="u1",
                classification={"source": "salesforce"},
            )
        )
    assert selections == [{"clarification_id": "c-entity", "option_id": "source:Contact"}]
