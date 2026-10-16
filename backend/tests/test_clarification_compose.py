import asyncio

from services.planning.clarification_compose import (
    _fallback_prompt,
    compose_clarification_prompt,
    resolve_clarification_display_message,
)
from services.planning.schemas import Clarification, ClarificationOption


def test_fallback_prompt_extract_fields_mentions_entity():
    clar = Clarification(
        id="c1",
        clarification_type="pick_extract_fields",
        prompt="Which columns or fields should we extract?",
        options=[
            ClarificationOption(id="field_all_descriptive", label="All descriptive columns"),
            ClarificationOption(id="field:Name", label="Name"),
        ],
    )
    text = _fallback_prompt(
        clar,
        classification={
            "source": "salesforce",
            "source_name": "SF 2",
            "source_object_api_name": "Contact",
        },
        user_prompt="extract contact details from sf 2",
    )
    assert "Contact" in text
    assert "SF 2" in text
    assert "field" in text.lower()


def test_compose_uses_fallback_when_llm_unavailable(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("no api")

    monkeypatch.setattr(
        "services.architect_agent.async_openai_call",
        _boom,
    )
    clar = Clarification(
        id="c1",
        clarification_type="pick_destination_connector",
        prompt="Where should we send or create the data?",
        options=[],
    )

    async def _run():
        return await compose_clarification_prompt(
            clar,
            classification={"source": "salesforce", "source_name": "SF 2"},
            user_prompt="extract company details from sf 2",
            user_id="u1",
        )

    out = asyncio.run(_run())
    assert out.prompt != "Where should we send or create the data?"
    assert "SF 2" in out.prompt or "extract" in out.prompt.lower()


def test_resolve_display_message_uses_fallback_for_empty_title_clar():
    clar = {
        "id": "t1",
        "clarification_type": "create_workbook_name",
        "prompt": "",
        "options": [],
        "required": True,
    }
    text = resolve_clarification_display_message(
        [clar],
        classification={
            "source_name": "SF 1",
            "destination_name": "Google Sheets",
        },
        user_prompt="export leads to new sheet",
    )
    assert "Google Sheet" in text or "name" in text.lower()
    assert "Please choose an option" not in text
