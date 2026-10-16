"""Tests for unified LLM clarification reply parsing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.planning.binding.clarification_resolve import (
    resolve_clarification_selections_llm,
)


def _llm_response(payload: str) -> MagicMock:
    msg = MagicMock()
    msg.content = payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_resolve_clarification_selections_llm_pick_create_headers():
    clar = [
        {
            "id": "c-headers",
            "clarification_type": "pick_create_headers",
            "required": True,
            "prompt": "Which columns should the new sheet have?",
            "options": [],
        }
    ]
    payload = (
        '{"selections": [{"clarification_id": "c-headers", '
        '"option_id": "fields_custom", "text_value": "Name, Email, Phone"}], '
        '"confidence": 0.91}'
    )

    with patch(
        "services.architect_agent.async_openai_call",
        new=AsyncMock(return_value=_llm_response(payload)),
    ):
        selections = asyncio.run(
            resolve_clarification_selections_llm(
                "Name, Email, Phone",
                clar,
                user_id="u1",
                classification={"intent": "create", "destination": "google_sheets"},
                prompt="create a google sheet with contact fields",
            )
        )

    assert len(selections) == 1
    assert selections[0]["clarification_id"] == "c-headers"
    assert selections[0]["option_id"] == "fields_custom"
    assert selections[0]["text_value"] == "Name, Email, Phone"


def test_resolve_clarification_selections_llm_low_confidence_returns_empty():
    clar = [
        {
            "id": "c-headers",
            "clarification_type": "pick_create_headers",
            "required": True,
            "options": [],
        }
    ]
    payload = '{"selections": [], "confidence": 0.2}'

    with patch(
        "services.architect_agent.async_openai_call",
        new=AsyncMock(return_value=_llm_response(payload)),
    ):
        selections = asyncio.run(
            resolve_clarification_selections_llm(
                "maybe something",
                clar,
                user_id="u1",
                classification={},
            )
        )

    assert selections == []


def test_resolve_clarification_selections_llm_delegates_entity_to_entity_resolver():
    clar = [
        {
            "id": "c-entity",
            "clarification_type": "pick_source_entity",
            "required": True,
            "options": [],
        }
    ]

    with patch(
        "services.planning.binding.clarification_resolve.resolve_entity_clarification_selections",
        new=AsyncMock(return_value=[{"clarification_id": "c-entity", "option_id": "Contact"}]),
    ) as mock_entity:
        selections = asyncio.run(
            resolve_clarification_selections_llm(
                "use contact object",
                clar,
                user_id="u1",
                classification={"source": "salesforce"},
            )
        )

    mock_entity.assert_awaited_once()
    assert selections[0]["option_id"] == "Contact"
