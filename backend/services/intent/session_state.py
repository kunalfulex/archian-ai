"""Active extraction session lookup (frontend history + DB fallback)."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from services.planning.conversation_session import intent_state_from_extraction_metadata


def _normalize_source_type(value: Optional[str]) -> str:
    v = (value or "").strip().lower()
    if v == "gsheets":
        return "google_sheets"
    return v or "unknown"


def _source_types_match(left: Optional[str], right: Optional[str]) -> bool:
    return _normalize_source_type(left) == _normalize_source_type(right)


def is_state_still_valid(conversation_history: List[Dict], extraction_event_index: int) -> bool:
    """
    True when intent_state is still valid (no migration_started after extraction).
    mapping_started does not invalidate intent_state.
    """
    if not conversation_history or extraction_event_index < 0:
        return True

    for msg in conversation_history[extraction_event_index + 1 :]:
        metadata = msg.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        if metadata.get("event_type") == "migration_started":
            return False
    return True


def extract_intent_state_from_history(
    conversation_history: Optional[List[Dict]],
    source_type: Optional[str] = None,
) -> Optional[Dict]:
    """Extract active intent_state from frontend conversation_history."""
    if not conversation_history:
        return None

    now = datetime.now(timezone.utc)

    for i, msg in enumerate(reversed(conversation_history)):
        metadata = msg.get("metadata", {})
        if not isinstance(metadata, dict):
            continue

        event_type = metadata.get("event_type")
        if event_type not in ("extraction_started", "extraction_completed"):
            continue

        intent_state = metadata.get("intent_state")
        if not intent_state or not isinstance(intent_state, dict):
            intent_state = intent_state_from_extraction_metadata(metadata)
        if not intent_state:
            continue

        if source_type and not _source_types_match(
            intent_state.get("active_source"), source_type
        ):
            continue

        if intent_state.get("is_active"):
            expires_at_str = intent_state.get("expires_at")
            if expires_at_str:
                try:
                    expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    if expires_at > now:
                        msg_index = len(conversation_history) - 1 - i
                        if is_state_still_valid(conversation_history, msg_index):
                            return intent_state
                except Exception as e:
                    print(f"⚠️ Error parsing expires_at: {e}")
                    continue

    return None


async def get_active_intent_state_from_db(
    conversation_id: str,
    user_id: str,
    source_type: Optional[str] = None,
) -> Optional[Dict]:
    """Query DB for active intent_state when frontend history is missing."""
    try:
        from db.connection import database

        base_query = """
            SELECT metadata, created_at FROM chat_messages
            WHERE conversation_id = :conversation_id
              AND user_id = :user_id
              AND role IN ('data_event', 'assistant')
              AND metadata->>'event_type' IN ('extraction_started', 'extraction_completed')
              AND (
                metadata->'intent_state'->>'is_active' = 'true'
                OR metadata->>'job_id' IS NOT NULL
              )
              AND (
                metadata->'intent_state'->>'expires_at' IS NULL
                OR (metadata->'intent_state'->>'expires_at')::timestamptz > NOW()
              )
        """

        if source_type:
            base_query += " AND metadata->'intent_state'->>'active_source' = :source_type"

        base_query += """
              AND NOT EXISTS (
                  SELECT 1 FROM chat_messages cm2
                  WHERE cm2.conversation_id = chat_messages.conversation_id
                    AND cm2.created_at > chat_messages.created_at
                    AND cm2.role = 'data_event'
                    AND cm2.metadata->>'event_type' = 'migration_started'
              )
            ORDER BY created_at DESC
            LIMIT 1
        """

        params: Dict[str, str] = {
            "conversation_id": conversation_id,
            "user_id": user_id,
        }
        if source_type:
            params["source_type"] = source_type

        state_message = await database.fetch_one(base_query, params)

        if state_message:
            metadata = state_message["metadata"]
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            intent_state = metadata.get("intent_state")
            if intent_state and isinstance(intent_state, dict):
                return intent_state
    except Exception as e:
        print(f"⚠️ Error querying intent_state from DB: {e}")

    return None


async def get_active_intent_state(
    conversation_id: Optional[str],
    user_id: str,
    conversation_history: Optional[List[Dict]] = None,
    source_type: Optional[str] = None,
    current_prompt: Optional[str] = None,
    job_id_hint: Optional[str] = None,
) -> Optional[Dict]:
    """
    Active extraction session metadata.

    Prefers DB-backed conversation history (with metadata) when conversation_id is set.
    Client conversation_history is a deprecated fallback when no conversation_id.
    """
    del current_prompt  # reserved
    db_history: Optional[List[Dict]] = None
    if conversation_id:
        try:
            from services.chat_service import ChatService

            db_history = await ChatService.get_messages_with_metadata_for_context(
                conversation_id, user_id, limit=25
            )
        except Exception as exc:
            print(f"⚠️ Error loading conversation history for intent_state: {exc}")

    if db_history:
        intent_state = extract_intent_state_from_history(db_history, source_type)
        if intent_state:
            if job_id_hint:
                state_job = (
                    intent_state.get("job_id")
                    or intent_state.get("active_job_id")
                )
                if state_job and str(state_job) != str(job_id_hint):
                    intent_state = None
            if intent_state:
                print(
                    f"✅ Found active intent_state from DB history: "
                    f"{intent_state.get('active_source')} (state: {intent_state.get('state')})"
                )
                return intent_state

    if conversation_history:
        intent_state = extract_intent_state_from_history(conversation_history, source_type)
        if intent_state:
            if job_id_hint:
                state_job = (
                    intent_state.get("job_id")
                    or intent_state.get("active_job_id")
                )
                if state_job and str(state_job) != str(job_id_hint):
                    intent_state = None
            if intent_state:
                print(
                    f"✅ Found active intent_state from client history: "
                    f"{intent_state.get('active_source')} (state: {intent_state.get('state')})"
                )
                return intent_state

    if conversation_id:
        intent_state = await get_active_intent_state_from_db(
            conversation_id, user_id, source_type
        )
        if intent_state:
            if job_id_hint:
                state_job = (
                    intent_state.get("job_id")
                    or intent_state.get("active_job_id")
                )
                if state_job and str(state_job) != str(job_id_hint):
                    return None
            print(
                f"✅ Found active intent_state from DB query: "
                f"{intent_state.get('active_source')} (state: {intent_state.get('state')})"
            )
            return intent_state

    return None


def create_intent_state(
    active_intent: str,
    active_source: str,
    active_destination: str,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    job_id: Optional[str] = None,
    state: str = "awaiting_connection",
    expires_in_hours: int = 24,
) -> Dict:
    """Create intent_state blob for extraction event metadata."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(hours=expires_in_hours)

    return {
        "active_intent": active_intent,
        "active_source": active_source,
        "active_destination": active_destination,
        "source_name": source_name,
        "destination_name": destination_name,
        "job_id": job_id,
        "state": state,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "is_active": True,
    }
