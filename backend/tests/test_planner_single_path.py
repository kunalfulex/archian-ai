"""Planner must not use architect shortcuts when PLAN_SCOPE scope exists."""
from __future__ import annotations

import asyncio

from agent.planner import Planner
from agent.session import AgentSession


def test_planner_no_architect_shortcut_when_plan_scope_present():
    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "destination": "sql",
            "intent": "extract",
            "_plan_scope": {"operations": ["extract", "map", "migrate"]},
            "execution_plan": {
                "steps": [{"parameters": {"tool_id": "extract_salesforce"}}],
                "status": "awaiting_approval",
            },
            "plan_status": "awaiting_approval",
        },
        prompt="extract contacts and migrate",
    )
    plan = asyncio.run(Planner().build_plan("extract contacts and migrate", session))
    assert plan.steps == []
    assert "plan_scoped" in (plan.reasoning or "")
