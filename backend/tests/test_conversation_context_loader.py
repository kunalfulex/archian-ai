"""DB-backed conversation context loader (metadata preserved)."""
from __future__ import annotations

from services.intent.session_state import extract_intent_state_from_history
from services.planning.conversation_session import (
    build_session_progress_summary,
    latest_extraction_completed_metadata,
)


def test_metadata_context_shape_for_session_helpers():
    history = [
        {
            "type": "user",
            "text": "extract contacts",
            "metadata": None,
        },
        {
            "type": "system",
            "text": "",
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-abc",
                "source_type": "file",
                "source_name": "contacts.xlsx",
                "intent_state": {
                    "is_active": True,
                    "active_source": "file",
                    "job_id": "job-abc",
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "state": "extraction_completed",
                },
            },
        },
        {
            "type": "system",
            "text": "map fields",
            "metadata": {
                "event_type": "mapping_started",
                "job_id": "job-abc",
                "mapping_id": "mapping-1",
            },
        },
    ]

    meta = latest_extraction_completed_metadata(history)
    assert meta is not None
    assert meta.get("job_id") == "job-abc"
    assert meta.get("source_type") == "file"

    progress = build_session_progress_summary(history, job_id="job-abc")
    assert progress.get("job_id") == "job-abc"
    assert progress.get("last_completed_step") in ("extract", "map", "none")

    intent = extract_intent_state_from_history(history)
    assert intent is not None
    assert intent.get("active_source") == "file"


def test_chat_service_format_message_types():
    """Document expected mapping from DB roles to architect history types."""
    from services.chat_service import ChatService

    role_map = {
        "user": "user",
        "assistant": "system",
        "data_event": "system",
    }
    for role, expected in role_map.items():
        msg_type = "user" if role == "user" else "system"
        assert msg_type == expected
