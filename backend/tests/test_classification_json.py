from services.planning.schemas import (
    json_safe_value,
    sanitize_classification_for_json,
)


def test_sanitize_classification_strips_file_bytes():
    raw = {
        "source": "file",
        "_pending_file_payload": {"filename": "a.csv", "content": b"hello"},
        "_pending_file_ref": {"file_key": "k", "filename": "a.csv", "size_bytes": 5},
    }
    safe = sanitize_classification_for_json(raw)
    assert "_pending_file_payload" not in safe
    assert safe["_pending_file_ref"]["file_key"] == "k"


def test_json_safe_value_handles_bytes():
    out = json_safe_value({"x": b"abc"})
    assert out["x"]["_bytes_omitted"] is True
    assert out["x"]["size"] == 3


def test_json_safe_value_handles_uuid():
    import uuid

    uid = uuid.uuid4()
    out = json_safe_value({"id": uid, "nested": [{"message_id": uid}]})
    assert out["id"] == str(uid)
    assert out["nested"][0]["message_id"] == str(uid)


def test_build_planner_pending_payload_omits_conversation_history():
    import uuid

    from services.planning.clarification_apply import build_planner_pending_payload

    msg_id = uuid.uuid4()
    pending = build_planner_pending_payload(
        {
            "intent": "create",
            "destination": "google_sheets",
            "execution_plan": {"steps": []},
        },
        prompt="create sheet",
        conversation_id=uuid.uuid4(),
        conversation_history=[{"id": msg_id, "role": "user", "content": "hi"}],
    )
    import json

    json.dumps(pending)
    assert "conversation_history" not in pending
