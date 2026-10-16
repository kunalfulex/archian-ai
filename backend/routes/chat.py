"""Chat conversation and message endpoints."""

import asyncio
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, validator

from routes.auth import get_current_user
from services.chat_service import ChatService
from services.extraction_results_service import ExtractionResultsService

router = APIRouter()


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = None


class ConversationMessageRequest(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None

    @validator("role")
    def validate_role(cls, value: str) -> str:
        allowed_roles = {"user", "assistant", "system"}
        if value not in allowed_roles:
            raise ValueError(f"Role must be one of {', '.join(sorted(allowed_roles))}")
        return value

    @validator("content")
    def validate_content(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Content cannot be empty")
        return value


class MessageMetadataUpdateRequest(BaseModel):
    metadata: Dict[str, Any]


@router.get("/chat/conversations")
async def list_chat_conversations(
    limit: int = Query(50, ge=1, le=200),
    user_id: UUID = Depends(get_current_user),
):
    """Return the most recent chat conversations for the current user."""
    conversations = await ChatService.list_conversations(str(user_id), limit=limit)
    return {"status": "success", "conversations": conversations}


@router.post("/chat/conversations")
async def create_chat_conversation(
    request: ConversationCreateRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Create a new chat conversation for the user."""
    conversation = await ChatService.create_conversation(str(user_id), request.title)
    return {"status": "success", "conversation": conversation}


@router.get("/chat/conversations/{conversation_id}/messages")
async def get_chat_conversation_messages(
    conversation_id: str,
    limit: int = Query(50, ge=1, le=500),
    slim: bool = Query(True),
    user_id: UUID = Depends(get_current_user),
):
    """Return ordered messages for a conversation."""
    conversation = await ChatService.get_conversation(conversation_id, str(user_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = await ChatService.get_messages(
        conversation_id,
        str(user_id),
        limit=limit,
        conversation_verified=True,
        slim=slim,
    )
    return {"status": "success", "messages": messages, "conversation": conversation}


@router.get("/chat/conversations/{conversation_id}/hydrate")
async def hydrate_chat_conversation(
    conversation_id: str,
    message_limit: int = Query(50, ge=1, le=500, alias="message_limit"),
    extraction_limit: int = Query(50, ge=1, le=200, alias="extraction_limit"),
    slim: bool = Query(True),
    user_id: UUID = Depends(get_current_user),
):
    """Load conversation messages and extraction summaries in one round trip."""
    conversation = await ChatService.get_conversation(conversation_id, str(user_id))
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_coro = ChatService.get_messages(
        conversation_id,
        str(user_id),
        limit=message_limit,
        conversation_verified=True,
        slim=slim,
    )
    extractions_coro = ExtractionResultsService.get_extraction_results_by_conversation(
        conversation_id,
        str(user_id),
        limit=extraction_limit,
        conversation_verified=True,
    )
    messages, extraction_results = await asyncio.gather(messages_coro, extractions_coro)
    metadata_results = [
        {
            "id": result["id"],
            "job_id": result["job_id"],
            "parent_job_id": result["parent_job_id"],
            "source_type": result["source_type"],
            "source_name": result["source_name"],
            "source_identifier": result["source_identifier"],
            "extraction_goals": result["extraction_goals"],
            "document_type": result["document_type"],
            "document_description": result["document_description"],
            "record_count": result["record_count"],
            "extraction_version": result["extraction_version"],
            "has_cleanup_rules": result["has_cleanup_rules"],
            "has_transformation_rules": result["has_transformation_rules"],
            "created_at": result["created_at"],
        }
        for result in extraction_results
    ]
    return {
        "status": "success",
        "conversation": conversation,
        "messages": messages,
        "results": metadata_results,
    }


@router.post("/chat/conversations/{conversation_id}/messages")
async def create_chat_conversation_message(
    conversation_id: str,
    request: ConversationMessageRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Store a chat message and update the conversation metadata.
    If conversation doesn't exist and this is a user message, it will be created automatically."""
    message, conversation = await ChatService.add_message(
        conversation_id,
        str(user_id),
        request.role,
        request.content,
        request.metadata,
    )

    if message is None or conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found or could not be created")

    return {"status": "success", "message": message, "conversation": conversation}


@router.put("/chat/conversations/{conversation_id}/messages/{message_id}/metadata")
async def update_chat_message_metadata(
    conversation_id: str,
    message_id: str,
    request: MessageMetadataUpdateRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Update the metadata of an existing chat message."""
    message = await ChatService.update_message_metadata(
        message_id,
        conversation_id,
        str(user_id),
        request.metadata,
    )

    if message is None:
        raise HTTPException(
            status_code=404,
            detail="Message not found or you don't have access to it"
        )

    return {"status": "success", "message": message}


class RecoveryDismissRequest(BaseModel):
    recovery_batch_id: Optional[str] = None
    recovery_job_id: Optional[str] = None


class PlanDismissRequest(BaseModel):
    plan_id: Optional[str] = None
    message_id: Optional[str] = None
    planner_pending: Optional[Dict[str, Any]] = None


@router.post("/chat/conversations/{conversation_id}/recovery-dismiss")
async def dismiss_migration_recovery(
    conversation_id: str,
    request: RecoveryDismissRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Persist recovery dismissal without re-running the migration pipeline."""
    try:
        from services.chat_service import ChatService

        await ChatService.log_data_event(
            str(conversation_id),
            str(user_id),
            "recovery_dismissed",
            {
                "recovery_batch_id": request.recovery_batch_id,
                "recovery_job_id": request.recovery_job_id,
            },
        )
    except Exception:
        pass
    try:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled() and request.recovery_batch_id:
            PipelineLearningService.schedule_capture(
                lambda: PipelineLearningService.capture_recovery_attempt(
                    user_id=str(user_id),
                    conversation_id=str(conversation_id),
                    batch_id=str(request.recovery_batch_id),
                    proposal={},
                    outcome="dismissed",
                )
            )
    except Exception:
        pass
    return {"status": "success"}


@router.post("/chat/conversations/{conversation_id}/plan-dismiss")
async def dismiss_execution_plan(
    conversation_id: str,
    request: PlanDismissRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Persist execution-plan dismissal without inferring the user's next intent."""
    pending = dict(request.planner_pending or {})
    classification = dict(pending.get("classification") or {})
    plan_id = (
        (request.plan_id or "").strip()
        or (pending.get("plan_id") or "").strip()
        or (classification.get("plan_id") or "").strip()
        or None
    )
    event_data: Dict[str, Any] = {
        "plan_dismissed": True,
        "planner_resolved": True,
        "plan_id": plan_id,
        "message_id": (request.message_id or "").strip() or None,
        "original_prompt": pending.get("original_prompt"),
    }
    if pending:
        event_data["planner_pending"] = pending

    try:
        from services.chat_service import ChatService

        await ChatService.log_data_event(
            str(conversation_id),
            str(user_id),
            "plan_dismissed",
            event_data,
        )
    except Exception:
        pass

    if request.message_id:
        try:
            from services.chat_service import ChatService

            await ChatService.update_message_metadata(
                str(request.message_id),
                str(conversation_id),
                str(user_id),
                {
                    "planner_resolved": True,
                    "plan_dismissed": True,
                    "plan_id": plan_id,
                },
            )
        except Exception:
            pass

    try:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled() and pending:
            PipelineLearningService.schedule_capture(
                lambda: PipelineLearningService.capture_plan_rejected(
                    user_id=str(user_id),
                    conversation_id=str(conversation_id),
                    prompt=(pending.get("original_prompt") or "").strip(),
                    classification=classification,
                    pending=pending,
                )
            )
    except Exception:
        pass

    return {"status": "success"}


@router.delete("/chat/conversations/{conversation_id}")
async def delete_chat_conversation(
    conversation_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Delete a chat conversation for the current user."""
    deleted = await ChatService.delete_conversation(conversation_id, str(user_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "success"}
