"""Tests for LLM clarification compose with suggestions."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from services.planning.clarification_compose import (
    _suggestions_to_options,
    compose_clarification_prompt,
)
from services.planning.schemas import Clarification


def _llm_response(payload: str) -> MagicMock:
    msg = MagicMock()
    msg.content = payload
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def test_suggestions_to_options_create_workbook():
    clar = Clarification(
        id="c1",
        clarification_type="create_workbook_name",
        prompt="",
        options=[],
    )
    opts = _suggestions_to_options(
        clar,
        [{"label": "Contact List"}, {"label": "Customer Export"}],
    )
    assert len(opts) == 2
    assert opts[0].id == "text:Contact List"


def test_suggestions_to_options_pick_create_headers():
    clar = Clarification(
        id="c1",
        clarification_type="pick_create_headers",
        prompt="",
        options=[],
    )
    opts = _suggestions_to_options(
        clar,
        [{"label": "First Name"}, {"label": "Email"}],
    )
    assert opts[0].id == "header:First Name"
    assert opts[1].id == "header:Email"
    assert opts[-1].id == "fields_custom"


def test_compose_attaches_suggestions_and_context(monkeypatch):
    payload = (
        '{"prompt": "What should we call the sheet?", '
        '"context_summary": "Create a contact sheet", '
        '"suggestions": [{"label": "Contact List", "option_id": "text:Contact List"}]}'
    )

    monkeypatch.setattr(
        "services.architect_agent.async_openai_call",
        AsyncMock(return_value=_llm_response(payload)),
    )
    clar = Clarification(
        id="c1",
        clarification_type="create_workbook_name",
        prompt="",
        options=[],
    )

    async def _run():
        return await compose_clarification_prompt(
            clar,
            classification={
                "intent": "create",
                "destination": "google_sheets",
                "planner_spreadsheet_headers": ["First Name", "Email"],
            },
            user_prompt="create google sheet with contact fields",
            user_id="u1",
        )

    out = asyncio.run(_run())
    assert "call the sheet" in out.prompt.lower()
    assert out.context_summary == "Create a contact sheet"
    assert len(out.options) == 1
    assert out.options[0].id == "text:Contact List"
