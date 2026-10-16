"""Phase 4 — continuation session-facts enrichment."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.continuation_session_enrich import (
    enrich_continuation_goal_from_session_facts,
)
from services.planning.session_facts import destination_phrase_is_generic


def test_destination_phrase_is_generic():
    assert destination_phrase_is_generic("")
    assert destination_phrase_is_generic("that sheet")
    assert destination_phrase_is_generic("  That Sheet  ")
    assert not destination_phrase_is_generic("SF 1 XYZ list")


def test_enrich_continuation_resolves_generic_phrase_from_session():
    history = [
        {
            "metadata": {
                "event_type": "spreadsheet_creation_completed",
                "spreadsheet_name": "Contact Information",
                "spreadsheet_id": "abc123",
            }
        }
    ]
    payload = (
        '{"destination_phrase": "Contact Information", '
        '"references_recent_spreadsheet": true, "confidence": 0.92}'
    )

    async def _run():
        with patch(
            "services.architect_agent.async_openai_call",
            new=AsyncMock(
                return_value=type(
                    "R",
                    (),
                    {
                        "choices": [
                            type(
                                "C",
                                (),
                                {"message": type("M", (), {"content": payload})()},
                            )()
                        ]
                    },
                )()
            ),
        ):
            return await enrich_continuation_goal_from_session_facts(
                {
                    "destination": "google_sheets",
                    "intent": "map_data",
                    "mode": "map",
                    "operations": ["map", "migrate"],
                    "_plan_scope": {
                        "destination_phrase": "that sheet",
                        "operations": ["map", "migrate"],
                    },
                },
                {"destination_phrase": "that sheet", "operations": ["map", "migrate"]},
                user_id="u1",
                prompt="move them to that sheet",
                conversation_history=history,
                job_id="job-1",
            )

    cls, goal = asyncio.run(_run())
    assert goal.get("destination_phrase") == "Contact Information"
    assert cls.get("destination_phrase") == "Contact Information"


def test_enrich_continuation_skips_without_history():
    async def _run():
        return await enrich_continuation_goal_from_session_facts(
            {"destination": "google_sheets"},
            {"destination_phrase": "that sheet"},
            user_id="u1",
            prompt="move them to that sheet",
            conversation_history=None,
        )

    cls, goal = asyncio.run(_run())
    assert goal.get("destination_phrase") == "that sheet"
    assert not cls.get("destination_name")


def test_destination_llm_skip_reason_continuation_no_map():
    from services.planning.planning_kernel import destination_llm_skip_reason

    reason = destination_llm_skip_reason(
        {"_continuation_steps": ["data_ops"], "plan_route": "continuation_path"},
        parsed_goal={"operations": ["filter"]},
        job=type("J", (), {"job_id": "j1", "mapping_ready": False})(),
        plan_route="continuation_path",
    )
    assert reason == "no_map_step"
