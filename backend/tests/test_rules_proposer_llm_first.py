"""LLM-first rule proposal: heuristics only as filter fallback."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from post_extract.rules_proposer import (
    _delta_is_meaningful,
    heuristic_rules_for_prompt,
    propose_rules_delta,
)

COMPOUND_PROMPT = (
    "combine first name, last name as name and remove any invalid email id records"
)
SCHEMA = ["Email", "FirstName", "LastName"]


def test_delta_is_meaningful_transform_merge():
    delta = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "Name",
                }
            ]
        }
    }
    assert _delta_is_meaningful("transform", delta) is True


def test_delta_is_meaningful_filter_only_not_transform():
    delta = {
        "filter_rules": {
            "conditions": [{"field": "Email", "operator": "valid_email"}],
            "logic": "AND",
        }
    }
    assert _delta_is_meaningful("transform", delta) is False
    assert _delta_is_meaningful("filter", delta) is True


def _mock_openai_response(payload: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = json.dumps(payload)
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    return response



def test_transform_compound_prompt_uses_llm_not_email_heuristic(monkeypatch):
    """Transform step must reach LLM for merge rules; email heuristic is filter-only."""

    async def _run():
        llm_payload = {
            "transformation_rules": {
                "fields": [
                    {
                        "action": "merge",
                        "source_fields": ["FirstName", "LastName"],
                        "output_field": "Name",
                    }
                ]
            }
        }
        mock_call = AsyncMock(return_value=_mock_openai_response(llm_payload))
        monkeypatch.setattr("services.architect_agent.async_openai_call", mock_call)

        delta = await propose_rules_delta(
            "transform",
            COMPOUND_PROMPT,
            [{"Email": "a@b.co", "FirstName": "A", "LastName": "B"}],
            SCHEMA,
        )

        assert mock_call.await_count == 1
        tr = delta.get("transformation_rules") or {}
        fields = tr.get("fields") if isinstance(tr, dict) else tr
        assert any(
            isinstance(f, dict) and f.get("action") == "merge" for f in (fields or [])
        )
        assert "filter_rules" not in delta

    asyncio.run(_run())


def test_transform_does_not_fallback_to_email_heuristic(monkeypatch):
    async def _run():
        mock_call = AsyncMock(return_value=_mock_openai_response({}))
        monkeypatch.setattr("services.architect_agent.async_openai_call", mock_call)

        delta = await propose_rules_delta(
            "transform",
            COMPOUND_PROMPT,
            [],
            SCHEMA,
        )

        assert delta == {}
        assert mock_call.await_count == 1

    asyncio.run(_run())


def test_filter_falls_back_to_heuristic_when_llm_empty(monkeypatch):
    async def _run():
        mock_call = AsyncMock(return_value=_mock_openai_response({}))
        monkeypatch.setattr("services.architect_agent.async_openai_call", mock_call)

        delta = await propose_rules_delta(
            "filter",
            "remove invalid email id records from the extracted data",
            [],
            SCHEMA,
        )

        assert mock_call.await_count == 1
        conds = (delta.get("filter_rules") or {}).get("conditions") or []
        assert conds[0]["field"] == "Email"
        assert conds[0]["operator"] == "valid_email"

    asyncio.run(_run())


def test_transform_prompt_requires_output_field_for_merge(monkeypatch):
    """Transform LLM user message must instruct output_field (not rely on executor default)."""

    async def _run():
        captured: list = []

        async def _capture_call(client, **kwargs):
            captured.append(kwargs.get("messages") or [])
            return _mock_openai_response({})

        monkeypatch.setattr("services.architect_agent.async_openai_call", _capture_call)

        await propose_rules_delta(
            "transform",
            COMPOUND_PROMPT,
            [],
            SCHEMA,
        )

        user_msg = (captured[0][1]["content"] if captured else "") or ""
        assert "current schema fields" in user_msg.lower()
        assert "split_2" in user_msg.lower() or "split" in user_msg.lower()

    asyncio.run(_run())


def test_heuristic_still_available_for_filter_prompts():
    rules = heuristic_rules_for_prompt(
        "remove invalid email id records from the extracted data",
        SCHEMA,
    )
    assert rules
    assert rules["filter_rules"]["conditions"][0]["operator"] == "valid_email"
