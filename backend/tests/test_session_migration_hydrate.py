"""Tests for migration context hydration vs approved plan destination."""
from __future__ import annotations

from agent.session import AgentSession
from services.migration.context import MigrationContext
from services.migration.session_migration import apply_context_to_session


def test_apply_context_preserves_approved_plan_destination():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="map",
        destination="salesforce",
        destination_name="SF 2",
        classification_snapshot={
            "destination": "salesforce",
            "destination_name": "SF 2",
            "planner_destination_confirmed": True,
        },
    )
    ctx = MigrationContext(
        user_id="u1",
        extraction_job_id="job-1",
        mapping={"Email": {"field": "Email"}},
        document_type="Contact",
        destination="salesforce",
        destination_name="SF 1",
        conversation_id="c1",
    )
    apply_context_to_session(session, ctx)
    assert session.destination_name == "SF 2"
    assert session.mapping_config == {"Email": {"field": "Email"}}
