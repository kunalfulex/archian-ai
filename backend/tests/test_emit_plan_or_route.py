"""Regression: info/general turns must not emit execution plans."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services.planning.intent_routing import should_route_to_general_response


def test_info_with_stale_execution_plan_routes_general_not_approval():
    prompt = "where do we store customer information in salesforce sf 1 org?"
    classification = {
        "intent": "info",
        "mode": "general",
        "plan_route": "info_path",
        "plan_status": "awaiting_approval",
        "execution_plan": {
            "plan_id": "p-stale",
            "status": "awaiting_approval",
            "steps": [{"step_type": "extract", "label": "Extract Contact"}],
        },
        "source": "salesforce",
        "source_name": "SF 1",
    }
    assert should_route_to_general_response(classification, prompt)


def test_emit_plan_or_route_info_skips_awaiting_approval():
    async def _run():
        from services.architect_agent import _emit_plan_or_route

        classification = {
            "intent": "info",
            "mode": "general",
            "plan_route": "info_path",
            "plan_status": "awaiting_approval",
            "execution_plan": {
                "plan_id": "p-stale",
                "status": "awaiting_approval",
                "steps": [{"step_type": "extract", "label": "Extract Contact"}],
            },
            "source": "salesforce",
            "source_name": "SF 1",
        }
        prompt = "where do we store customer information in salesforce sf 1 org?"
        routed = []

        async def _fake_route(*args, **kwargs):
            routed.append(True)
            yield {"type": "assistant_message", "content": "info response"}

        with patch(
            "services.architect_agent._route_architect_after_classification",
            side_effect=_fake_route,
        ), patch("agent.events.planner_plan_ready") as mock_plan_ready:
            chunks = []
            async for chunk in _emit_plan_or_route(
                classification,
                prompt,
                [],
                "user-1",
                "conv-1",
            ):
                chunks.append(chunk)
            assert routed
            mock_plan_ready.assert_not_called()
            assert "execution_plan" not in classification
            return chunks

    asyncio.run(_run())
