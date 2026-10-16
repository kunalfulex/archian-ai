"""Architect chat, streaming, and cancel-jobs endpoints."""

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import services.architect_agent as architect
from routes.auth import get_current_user
from services.chat_service import ChatService
from utils.job_stores import assessment_jobs, extraction_jobs, migration_jobs, sql_tasks

router = APIRouter()


async def _load_conversation_history(
    conversation_id: Optional[str],
    fallback_history: Optional[List[Dict[str, Any]]],
    user_id: UUID
) -> List[Dict[str, Any]]:
    """
    Load conversation history for architect / classification.

    When conversation_id is set, always loads from DB (metadata included).
    Client-provided conversation_history is deprecated and ignored.
    """
    logger = logging.getLogger(__name__)
    if fallback_history:
        logger.warning(
            "conversation_history in request body is deprecated; loading from DB "
            "for conversation_id=%s",
            conversation_id,
        )

    if conversation_id:
        conversation = await ChatService.get_conversation(conversation_id, str(user_id))
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return await ChatService.get_messages_with_metadata_for_context(
            conversation_id,
            str(user_id),
            conversation_verified=True,
        )

    return list(fallback_history or [])


class PlannerResumeRequest(BaseModel):
    """Resume architect stream after planner v2 clarifications."""

    selections: List[Dict[str, Any]] = Field(default_factory=list)
    planner_pending: Dict[str, Any] = Field(default_factory=dict)
    reply_text: Optional[str] = None
    approve_plan: bool = False
    approve_schema_deploy: bool = False
    skip_schema_deploy: bool = False
    approve_recovery: bool = False
    skip_recovery: bool = False


class ArchitectPromptRequest(BaseModel):
    prompt: str
    conversation_history: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Deprecated — backend loads history from DB when conversation_id is set.",
    )
    conversation_id: Optional[str] = None
    planner_resume: Optional[PlannerResumeRequest] = None
    plan_auto_run: bool = False


@router.post("/edison/architect")
async def edison_architect_chat(request: ArchitectPromptRequest, user_id: UUID = Depends(get_current_user)):
    """
    Main architect endpoint with conversation context support.
    """
    try:
        # Validate conversation access if ID supplied
        await _load_conversation_history(request.conversation_id, request.conversation_history, user_id)

        # Use the original function with conversation context
        reply = await architect.handle_text_prompt_for_architect_agent(
            request.prompt, 
            user_id
        )
        
        if isinstance(reply, dict):
            return {"reply": reply.get("text_response", "⚠️ No explanation returned.")}
        else:
            # reply is already a string error message
            return {"reply": reply}
    except Exception as e:
        print("Architect chat error:", e)
        raise HTTPException(status_code=500, detail="Archian AI Architect failed to respond.")


@router.post("/architect/stream")
async def edison_architect_chat_stream(
    request: Request,
    user_id: UUID = Depends(get_current_user),
):
    """
    Streaming architect chat. Accepts JSON or multipart/form-data (file + prompt).
    """
    try:
        pending_file_upload = None
        content_type = (request.headers.get("content-type") or "").lower()

        if "multipart/form-data" in content_type:
            form = await request.form()
            prompt = str(form.get("prompt") or "")
            conversation_id = form.get("conversation_id")
            conversation_id = str(conversation_id) if conversation_id else None
            plan_auto_run = str(form.get("plan_auto_run") or "").lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            planner_resume = None
            pr_raw = form.get("planner_resume")
            if pr_raw:
                try:
                    planner_resume = json.loads(pr_raw) if isinstance(pr_raw, str) else pr_raw
                except json.JSONDecodeError:
                    planner_resume = None
            file_part = form.get("file")
            if file_part is not None and hasattr(file_part, "read"):
                file_bytes = await file_part.read()
                filename = getattr(file_part, "filename", None) or "upload"
                pending_file_upload = {"filename": filename, "content": file_bytes}
            history = await _load_conversation_history(conversation_id, [], user_id)
        else:
            body = await request.json()
            req = ArchitectPromptRequest.model_validate(body)
            prompt = req.prompt
            conversation_id = req.conversation_id
            plan_auto_run = bool(req.plan_auto_run)
            history = await _load_conversation_history(
                req.conversation_id, req.conversation_history, user_id
            )
            planner_resume = None
            if req.planner_resume:
                planner_resume = {
                    "selections": req.planner_resume.selections,
                    "planner_pending": req.planner_resume.planner_pending,
                    "reply_text": req.planner_resume.reply_text,
                    "approve_plan": req.planner_resume.approve_plan,
                    "approve_schema_deploy": req.planner_resume.approve_schema_deploy,
                    "skip_schema_deploy": req.planner_resume.skip_schema_deploy,
                    "approve_recovery": req.planner_resume.approve_recovery,
                    "skip_recovery": req.planner_resume.skip_recovery,
                }
                if planner_resume.get("approve_plan"):
                    from services.planning.map_flow_debug import log_map_flow

                    pending_raw = planner_resume.get("planner_pending") or {}
                    log_map_flow(
                        "architect_stream_planner_resume",
                        approve_plan=True,
                        conversation_id=conversation_id,
                        pending_plan_id=pending_raw.get("plan_id"),
                        pending_job_id=pending_raw.get("job_id"),
                    )

        generator = architect.handle_text_prompt_for_architect_agent_stream_real(
            prompt,
            history,
            user_id,
            conversation_id=conversation_id,
            planner_resume=planner_resume,
            plan_auto_run=plan_auto_run,
            pending_file_upload=pending_file_upload,
        )

        return StreamingResponse(
            generator,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail="Archian AI Architect streaming failed.")


class CancelJobsRequest(BaseModel):
    """Request body for cancelling in-flight jobs"""
    job_ids: List[str] = Field(default_factory=list, description="Migration and extraction job IDs to cancel")


@router.post("/edison/cancel-jobs")
async def cancel_jobs_endpoint(request: CancelJobsRequest, user_id: UUID = Depends(get_current_user)):
    """
    Cancel in-flight migration and extraction jobs.
    Called when user clicks Stop/Abort in the chat UI.
    """
    cancelled = []
    not_found = []
    for job_id in request.job_ids or []:
        if await migration_jobs.exists(job_id):
            await migration_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
            cancelled.append(job_id)
        elif await extraction_jobs.exists(job_id):
            await extraction_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
            cancelled.append(job_id)
        elif await sql_tasks.exists(job_id):
            await sql_tasks.update(job_id, {"cancelled": True, "status": "cancelled"})
            cancelled.append(job_id)
        elif await assessment_jobs.exists(job_id):
            await assessment_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
            from services.data_assessment import store as assessment_store

            await assessment_store.update_job(job_id, str(user_id), status="cancelled")
            cancelled.append(job_id)
        else:
            not_found.append(job_id)
    return JSONResponse(content={"status": "ok", "cancelled": cancelled, "not_found": not_found})
