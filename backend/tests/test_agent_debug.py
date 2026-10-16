from agent.tools.map_migrate_tools import _resolve_mapping_from_session
from agent.session import AgentSession


def test_resolve_mapping_from_session_uses_mapping_started_metadata():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="migrate",
        active_job_id="job-abc",
        conversation_history=[
            {
                "type": "assistant",
                "metadata": {
                    "event_type": "mapping_started",
                    "job_id": "job-abc",
                    "mapping_id": "map-1",
                    "mapping_config": {
                        "Email": {"object": "Contact", "field": "Email"},
                    },
                },
            }
        ],
    )
    resolved = _resolve_mapping_from_session(session, "job-abc")
    assert resolved.get("mapping_id") == "map-1"
    assert "Email" in resolved.get("mapping_config", {})
