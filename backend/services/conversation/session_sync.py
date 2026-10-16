"""Sync ConversationSession after pipeline milestones."""
from __future__ import annotations

from typing import Any, Dict, Optional


async def sync_session_after_extraction(
    user_id: str,
    conversation_id: Optional[str],
    *,
    job_id: str,
    intent_state: Optional[Dict[str, Any]] = None,
) -> None:
    if not conversation_id or not user_id:
        return
    from services.conversation.session_store import refresh_pipeline_progress, update_session

    await update_session(
        str(user_id),
        str(conversation_id),
        active_job_id=str(job_id),
        intent_state=intent_state,
    )
    await refresh_pipeline_progress(str(user_id), str(conversation_id), job_id=str(job_id))


async def sync_session_after_mapping(
    user_id: str,
    conversation_id: Optional[str],
    *,
    job_id: str,
    message_id: Optional[str] = None,
    mapping_version: Optional[int] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
) -> None:
    if not conversation_id or not user_id:
        return
    from services.conversation.session_store import refresh_pipeline_progress, update_session

    await update_session(
        str(user_id),
        str(conversation_id),
        active_job_id=str(job_id),
        latest_mapping_message_id=message_id,
        mapping_version=mapping_version,
        mapping_draft=mapping_draft,
    )
    await refresh_pipeline_progress(str(user_id), str(conversation_id), job_id=str(job_id))


async def sync_session_after_planner(
    user_id: str,
    conversation_id: Optional[str],
    *,
    message_id: Optional[str] = None,
    intent_state: Optional[Dict[str, Any]] = None,
) -> None:
    if not conversation_id or not user_id:
        return
    from services.conversation.session_store import update_session

    await update_session(
        str(user_id),
        str(conversation_id),
        planner_pending_message_id=message_id,
        intent_state=intent_state,
        merge_intent_state=True,
    )


async def sync_session_after_migration(
    user_id: str,
    conversation_id: Optional[str],
    *,
    job_id: str,
) -> None:
    """Refresh pipeline progress and pin active extraction job after migrate."""
    if not conversation_id or not user_id:
        return
    from services.conversation.session_store import refresh_pipeline_progress

    await refresh_pipeline_progress(str(user_id), str(conversation_id), job_id=str(job_id))


async def sync_session_after_post_extract(
    user_id: str,
    conversation_id: Optional[str],
    *,
    job_id: str,
) -> None:
    """Post-extract child job becomes the active records job for map/migrate/save-flow."""
    await sync_session_after_extraction(user_id, conversation_id, job_id=str(job_id))
