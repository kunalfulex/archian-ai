"""Tests for empty-plan recovery at SSE emit."""
from __future__ import annotations

import asyncio
import uuid

from services.planning.plan_emit_recovery import try_recover_empty_plan_at_emit
from services.planning.plan_scope import sync_scope_into_classification
from services.planning.schemas import ExecutionPlan
from tests.plan_scope_helpers import with_plan_scope


def test_recover_create_only_title_on_destination_name():
    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Kunal GS",
        "spreadsheet_headers": ["Name", "Email"],
    }
    classification = sync_scope_into_classification(
        {
            "destination": "google_sheets",
            "intent": "create",
            "planner_create_spreadsheet": True,
            "planner_spreadsheet_title_confirmed": True,
            "destination_name": "Kunal GS",
            "planner_spreadsheet_headers": ["Name", "Email"],
            "_destination_endpoint_clarified": True,
            "_plan_scope": goal,
        },
        goal,
        ["create"],
    )
    plan = ExecutionPlan(plan_id=str(uuid.uuid4()), status="draft", steps=[])

    out, ep, recovered = asyncio.run(
        try_recover_empty_plan_at_emit(
            classification,
            plan,
            prompt="create google sheet Kunal GS",
            user_id="user-1",
        )
    )
    assert recovered is True
    assert len(ep.steps) == 1
    assert out.get("plan_status") == "awaiting_approval"
