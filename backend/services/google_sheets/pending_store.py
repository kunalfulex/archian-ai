"""Persistent pending Google Sheets chat actions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from db.connection import database

# Fallback when migration not applied
_memory_pending: Dict[str, Dict[str, Any]] = {}


async def save_pending(user_id: str, payload: Dict[str, Any], action_type: str = "upsert_confirm") -> None:
    uid = str(user_id)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    try:
        await database.execute(
            """
            DELETE FROM chat_pending_sheet_actions WHERE user_id = :user_id
            """,
            {"user_id": uid},
        )
        await database.execute(
            """
            INSERT INTO chat_pending_sheet_actions (user_id, action_type, payload_json, expires_at)
            VALUES (:user_id, :action_type, :payload_json, :expires_at)
            """,
            {
                "user_id": uid,
                "action_type": action_type,
                "payload_json": json.dumps(payload),
                "expires_at": expires,
            },
        )
    except Exception as e:
        print(f"⚠️ [pending_store] DB unavailable, using memory: {e}")
        _memory_pending[uid] = payload


async def load_pending(user_id: str) -> Optional[Dict[str, Any]]:
    uid = str(user_id)
    try:
        row = await database.fetch_one(
            """
            SELECT payload_json FROM chat_pending_sheet_actions
            WHERE user_id = :user_id AND expires_at > NOW()
            ORDER BY created_at DESC LIMIT 1
            """,
            {"user_id": uid},
        )
        if row:
            raw = row["payload_json"]
            if isinstance(raw, str):
                return json.loads(raw)
            return dict(raw)
    except Exception:
        pass
    return _memory_pending.get(uid)


async def clear_pending(user_id: str) -> None:
    uid = str(user_id)
    try:
        await database.execute(
            "DELETE FROM chat_pending_sheet_actions WHERE user_id = :user_id",
            {"user_id": uid},
        )
    except Exception:
        pass
    _memory_pending.pop(uid, None)


def parse_confirmation_action(prompt: str) -> Dict[str, Any]:
    prompt_lower = prompt.lower().strip()
    if prompt_lower in ("update", "yes", "y", "confirm", "replace"):
        return {"has_pending": True, "action": "update"}
    if prompt_lower in ("add new", "add", "new", "no", "n", "skip"):
        return {"has_pending": True, "action": "add_new"}
    if prompt_lower in ("cancel", "abort", "stop"):
        return {"has_pending": True, "action": "cancel"}
    if prompt_lower.isdigit():
        return {
            "has_pending": True,
            "action": "select_number",
            "selected_index": int(prompt_lower) - 1,
        }
    return {"has_pending": False, "action": None}
