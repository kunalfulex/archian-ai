"""Conversation action endpoints (map, migrate, save-flow)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from uuid import UUID

from routes.auth import get_current_user
from services.chat_service import ChatService

router = APIRouter(prefix="/conversations", tags=["conversation-actions"])


class MapActionRequest(BaseModel):
    job_id: Optional[str] = None
    destination_name: Optional[str] = None
    destination_entity: Optional[str] = None


class MigrateActionRequest(BaseModel):
    job_id: Optional[str] = None
    mapping_message_id: Optional[str] = None


class SaveFlowActionRequest(BaseModel):
    flow_name: str
    job_id: Optional[str] = None
    mapping_message_id: Optional[str] = None
    batch_id: Optional[str] = None


class MappingDraftPatchRequest(BaseModel):
    job_id: Optional[str] = None
    mapping_config: Optional[Dict[str, Any]] = None
    transformation_rules: Optional[Dict[str, Any]] = None
    missing_field_preference: Optional[Dict[str, Any]] = None
    mapping_instruction: Optional[str] = None
    destination_object_api_name: Optional[str] = None
    table_name: Optional[str] = None
    destination: Optional[str] = None
    destination_name: Optional[str] = None


async def _ensure_conversation(conversation_id: str, user_id: UUID) -> None:
    conv = await ChatService.get_conversation(conversation_id, str(user_id))
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/{conversation_id}/actions/map")
async def conversation_action_map(
    conversation_id: str,
    body: MapActionRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _ensure_conversation(conversation_id, user_id)
    from services.conversation.actions.map import run_map_action

    try:
        return await run_map_action(
            user_id=str(user_id),
            conversation_id=conversation_id,
            job_id=body.job_id,
            destination_name=body.destination_name,
            destination_entity=body.destination_entity,
            remap=False,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{conversation_id}/actions/remap")
async def conversation_action_remap(
    conversation_id: str,
    body: MapActionRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _ensure_conversation(conversation_id, user_id)
    from services.conversation.actions.map import run_map_action

    try:
        return await run_map_action(
            user_id=str(user_id),
            conversation_id=conversation_id,
            job_id=body.job_id,
            destination_name=body.destination_name,
            destination_entity=body.destination_entity,
            remap=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{conversation_id}/actions/migrate")
async def conversation_action_migrate(
    conversation_id: str,
    body: MigrateActionRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _ensure_conversation(conversation_id, user_id)
    from services.conversation.actions.migrate import run_migrate_action
    from utils.migration_sse_stream import migration_sse_streaming_response

    try:
        result = await run_migrate_action(
            user_id=str(user_id),
            conversation_id=conversation_id,
            job_id=body.job_id,
            mapping_message_id=body.mapping_message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message") or "Migration failed")

    stream_resp = migration_sse_streaming_response(
        conversation_id,
        job_id=result.get("job_id"),
        batch_id=result.get("batch_id"),
        destination=result.get("destination"),
        destination_name=result.get("destination_name"),
        total_records=result.get("total_records"),
        source_job_id=result.get("source_job_id"),
        user_id=str(user_id),
    )
    if stream_resp:
        return stream_resp
    return result


@router.post("/{conversation_id}/actions/save-flow")
async def conversation_action_save_flow(
    conversation_id: str,
    body: SaveFlowActionRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _ensure_conversation(conversation_id, user_id)
    from services.conversation.actions.save_flow import run_save_flow_action

    try:
        return await run_save_flow_action(
            user_id=str(user_id),
            conversation_id=conversation_id,
            flow_name=body.flow_name,
            job_id=body.job_id,
            mapping_message_id=body.mapping_message_id,
            batch_id=body.batch_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/{conversation_id}/session/mapping-draft")
async def patch_conversation_mapping_draft(
    conversation_id: str,
    body: MappingDraftPatchRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _ensure_conversation(conversation_id, user_id)
    from services.conversation.actions.mapping_draft import patch_mapping_draft

    draft = body.model_dump(exclude_none=True)
    if not draft:
        raise HTTPException(status_code=400, detail="No draft fields provided")
    return await patch_mapping_draft(str(user_id), conversation_id, draft)
