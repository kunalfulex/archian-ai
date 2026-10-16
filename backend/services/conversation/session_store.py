"""Redis-backed conversation session (hot path) with DB hydrate on miss."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from utils.redis_service import DEFAULT_JOB_TTL, get_redis_client

SESSION_TTL_SECONDS = DEFAULT_JOB_TTL
_fallback_store: Dict[str, Dict[str, Any]] = {}


def _session_key(user_id: str, conversation_id: str) -> str:
    return f"conv_session:{user_id}:{conversation_id}"


@dataclass
class ConversationSession:
    user_id: str
    conversation_id: str
    active_job_id: Optional[str] = None
    intent_state: Optional[Dict[str, Any]] = None
    pipeline_progress: Optional[Dict[str, Any]] = None
    latest_mapping_message_id: Optional[str] = None
    mapping_draft: Optional[Dict[str, Any]] = None
    mapping_version: int = 0
    planner_pending_message_id: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "conversation_id": self.conversation_id,
            "active_job_id": self.active_job_id,
            "intent_state": self.intent_state,
            "pipeline_progress": self.pipeline_progress,
            "latest_mapping_message_id": self.latest_mapping_message_id,
            "mapping_draft": self.mapping_draft,
            "mapping_version": self.mapping_version,
            "planner_pending_message_id": self.planner_pending_message_id,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, user_id: str, conversation_id: str, data: Dict[str, Any]) -> "ConversationSession":
        return cls(
            user_id=user_id,
            conversation_id=conversation_id,
            active_job_id=data.get("active_job_id"),
            intent_state=data.get("intent_state"),
            pipeline_progress=data.get("pipeline_progress"),
            latest_mapping_message_id=data.get("latest_mapping_message_id"),
            mapping_draft=data.get("mapping_draft"),
            mapping_version=int(data.get("mapping_version") or 0),
            planner_pending_message_id=data.get("planner_pending_message_id"),
            updated_at=data.get("updated_at"),
        )


async def _read_raw(user_id: str, conversation_id: str) -> Optional[Dict[str, Any]]:
    key = _session_key(user_id, conversation_id)
    client = await get_redis_client()
    if client is not None:
        try:
            raw = await client.get(key)
            if raw:
                data = json.loads(raw)
                return data if isinstance(data, dict) else None
        except Exception as exc:
            print(f"⚠️ conv_session read failed: {exc}")
    return _fallback_store.get(key)


async def _write_raw(user_id: str, conversation_id: str, data: Dict[str, Any]) -> None:
    key = _session_key(user_id, conversation_id)
    payload = json.dumps(data, default=str)
    client = await get_redis_client()
    if client is not None:
        try:
            await client.setex(key, SESSION_TTL_SECONDS, payload)
            return
        except Exception as exc:
            print(f"⚠️ conv_session write failed: {exc}")
    _fallback_store[key] = dict(data)


async def load_session(user_id: str, conversation_id: str) -> Optional[ConversationSession]:
    """Load session from Redis/in-memory; returns None on miss."""
    if not user_id or not conversation_id:
        return None
    raw = await _read_raw(str(user_id), str(conversation_id))
    if not raw:
        return None
    return ConversationSession.from_dict(str(user_id), str(conversation_id), raw)


async def hydrate_from_db(user_id: str, conversation_id: str) -> ConversationSession:
    """Rebuild session snapshot from chat_messages metadata + latest extraction job."""
    from services.chat_service import ChatService
    from services.planning.conversation_session import (
        build_session_progress_summary,
        intent_state_from_extraction_metadata,
        latest_extraction_completed_metadata,
        latest_mapping_started_metadata,
    )

    history = await ChatService.get_messages_with_metadata_for_context(
        str(conversation_id), str(user_id), limit=25
    )
    ext_meta = latest_extraction_completed_metadata(history) or {}
    job_id = ext_meta.get("job_id")
    intent_state = None
    if ext_meta:
        intent_state = ext_meta.get("intent_state")
        if not isinstance(intent_state, dict):
            intent_state = intent_state_from_extraction_metadata(ext_meta)

    progress = build_session_progress_summary(history, job_id=str(job_id) if job_id else None)
    map_meta = latest_mapping_started_metadata(history, job_id=str(job_id) if job_id else None)
    mapping_message_id = None
    mapping_version = 0
    mapping_draft = None
    if map_meta:
        mapping_message_id = map_meta.get("message_id")
        try:
            mapping_version = int(map_meta.get("mapping_version") or 0)
        except (TypeError, ValueError):
            mapping_version = 0
        cfg = map_meta.get("mapping_config")
        if isinstance(cfg, dict) and cfg:
            mapping_draft = {"mapping_config": dict(cfg)}

    planner_msg_id = None
    for msg in reversed(history or []):
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), dict) else {}
        if meta.get("planner_pending") and meta.get("event_type") in (
            "planner_plan_ready",
            "planner_clarification_required",
        ):
            planner_msg_id = msg.get("id")
            break

    now = datetime.now(timezone.utc).isoformat()
    session = ConversationSession(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        active_job_id=str(job_id) if job_id else None,
        intent_state=intent_state,
        pipeline_progress=progress,
        latest_mapping_message_id=str(mapping_message_id) if mapping_message_id else None,
        mapping_draft=mapping_draft,
        mapping_version=mapping_version,
        planner_pending_message_id=str(planner_msg_id) if planner_msg_id else None,
        updated_at=now,
    )
    await _write_raw(str(user_id), str(conversation_id), session.to_dict())
    return session


async def load_or_hydrate_session(user_id: str, conversation_id: str) -> ConversationSession:
    """Load from Redis; hydrate from DB on miss."""
    existing = await load_session(user_id, conversation_id)
    if existing is not None:
        return existing
    return await hydrate_from_db(user_id, conversation_id)


async def update_session(
    user_id: str,
    conversation_id: str,
    *,
    active_job_id: Optional[str] = None,
    intent_state: Optional[Dict[str, Any]] = None,
    pipeline_progress: Optional[Dict[str, Any]] = None,
    latest_mapping_message_id: Optional[str] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
    mapping_version: Optional[int] = None,
    planner_pending_message_id: Optional[str] = None,
    merge_intent_state: bool = False,
) -> ConversationSession:
    """Patch session fields and persist."""
    session = await load_session(user_id, conversation_id)
    if session is None:
        session = ConversationSession(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
        )

    if active_job_id is not None:
        session.active_job_id = active_job_id
    if intent_state is not None:
        if merge_intent_state and isinstance(session.intent_state, dict):
            merged = dict(session.intent_state)
            merged.update(intent_state)
            session.intent_state = merged
        else:
            session.intent_state = intent_state
    if pipeline_progress is not None:
        session.pipeline_progress = pipeline_progress
    if latest_mapping_message_id is not None:
        session.latest_mapping_message_id = latest_mapping_message_id
    if mapping_draft is not None:
        session.mapping_draft = mapping_draft
    if mapping_version is not None:
        session.mapping_version = mapping_version
    if planner_pending_message_id is not None:
        session.planner_pending_message_id = planner_pending_message_id

    session.updated_at = datetime.now(timezone.utc).isoformat()
    await _write_raw(str(user_id), str(conversation_id), session.to_dict())
    return session


async def refresh_pipeline_progress(
    user_id: str,
    conversation_id: str,
    *,
    job_id: Optional[str] = None,
) -> ConversationSession:
    """Recompute pipeline_progress from DB and update session."""
    from services.chat_service import ChatService
    from services.planning.conversation_session import build_session_progress_summary

    history = await ChatService.get_messages_with_metadata_for_context(
        str(conversation_id), str(user_id), limit=25
    )
    progress = build_session_progress_summary(history, job_id=job_id)
    return await update_session(
        user_id,
        conversation_id,
        pipeline_progress=progress,
        active_job_id=job_id or progress.get("job_id"),
    )
