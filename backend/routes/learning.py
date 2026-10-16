"""Pipeline learning API routes."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from services.pipeline_learning.service import PipelineLearningService

router = APIRouter(prefix="/api/learning", tags=["learning"])


class MappingFinalizeRequest(BaseModel):
    conversation_id: str
    message_id: Optional[str] = None
    job_id: Optional[str] = None
    mapping_config: Dict[str, Any] = Field(default_factory=dict)
    transformation_rules: Optional[Dict[str, Any]] = None
    mapping_plan: Optional[Dict[str, Any]] = None
    destination_object: Optional[str] = None


class PlanRejectedRequest(BaseModel):
    conversation_id: Optional[str] = None
    original_prompt: Optional[str] = None
    edited_prompt: Optional[str] = None
    planner_pending: Dict[str, Any] = Field(default_factory=dict)


@router.post("/plan-rejected")
async def plan_rejected(
    body: PlanRejectedRequest,
    user_id: UUID = Depends(get_current_user),
):
    pending = body.planner_pending or {}
    classification = dict(pending.get("classification") or {})
    prompt = (body.original_prompt or pending.get("original_prompt") or "").strip()
    await PipelineLearningService.capture_plan_rejected(
        user_id=str(user_id),
        conversation_id=body.conversation_id,
        prompt=prompt,
        classification=classification,
        pending=pending,
        edited_prompt=(body.edited_prompt or "").strip() or None,
    )
    from services.labeling.capture_hooks import schedule_plan_label_evidence

    schedule_plan_label_evidence(
        user_id=str(user_id),
        conversation_id=body.conversation_id,
        prompt=prompt,
        classification=classification,
        event_type="plan_rejected",
    )
    return {"ok": True}


@router.post("/mapping-finalize")
async def mapping_finalize(
    body: MappingFinalizeRequest,
    user_id: UUID = Depends(get_current_user),
):
    await PipelineLearningService.finalize_mapping(
        user_id=str(user_id),
        conversation_id=body.conversation_id,
        message_id=body.message_id,
        job_id=body.job_id,
        mapping_config=body.mapping_config,
        transformation_rules=body.transformation_rules,
        mapping_plan=body.mapping_plan,
        destination_object=body.destination_object,
    )
    from services.labeling.capture_hooks import schedule_mapping_label_evidence

    schedule_mapping_label_evidence(
        user_id=str(user_id),
        conversation_id=body.conversation_id,
        mapping_config=body.mapping_config,
        destination_object=body.destination_object,
    )
    return {"ok": True}


@router.get("/similar")
async def similar_pipelines(
    query: str = Query(..., min_length=1),
    stage: Optional[str] = Query(None),
    chunk_type: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    destination_type: Optional[str] = Query(None),
    top_k: Optional[int] = Query(None, ge=1, le=20),
    user_id: UUID = Depends(get_current_user),
):
    chunks = await PipelineLearningService.retrieve(
        user_id=str(user_id),
        query_text=query,
        stage=stage,
        chunk_type=chunk_type,
        source_type=source_type,
        destination_type=destination_type,
        top_k=top_k,
    )
    return {"chunks": chunks, "count": len(chunks)}
