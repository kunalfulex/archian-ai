"""Super-admin API for pipeline learning / RAG observability."""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from routes.auth import get_super_admin_user
from services.pipeline_learning.metrics import LearningMetricsService

router = APIRouter(prefix="/admin/learning", tags=["admin-learning"])


@router.get("/summary")
async def admin_learning_summary(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[UUID] = None,
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.platform_summary(
        days=days,
        user_id=str(user_id) if user_id else None,
        source_type=source_type,
        destination_type=destination_type,
    )
    return {"status": "success", **data}


@router.get("/funnel")
async def admin_learning_funnel(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[UUID] = None,
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.stage_funnel(
        days=days,
        user_id=str(user_id) if user_id else None,
    )
    return {"status": "success", **data}


@router.get("/retrieval")
async def admin_learning_retrieval(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[UUID] = None,
    caller: Optional[str] = None,
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.retrieval_summary(
        days=days,
        user_id=str(user_id) if user_id else None,
        caller=caller,
    )
    return {"status": "success", **data}


@router.get("/chunks")
async def admin_learning_chunks(
    stage: Optional[str] = None,
    chunk_type: Optional[str] = None,
    user_id: Optional[UUID] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.list_chunks(
        stage=stage,
        chunk_type=chunk_type,
        user_id=str(user_id) if user_id else None,
        limit=limit,
        offset=offset,
    )
    return {"status": "success", **data}


@router.get("/runs/{run_id}/timeline")
async def admin_learning_run_timeline(
    run_id: UUID,
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.run_timeline(str(run_id))
    if not data.get("found"):
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return {"status": "success", **data}


@router.get("/effectiveness")
async def admin_learning_effectiveness(
    days: int = Query(30, ge=1, le=365),
    user_id: Optional[UUID] = None,
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.retrieval_effectiveness(
        days=days,
        user_id=str(user_id) if user_id else None,
    )
    return {"status": "success", **data}


@router.get("/tool-audit")
async def admin_learning_tool_audit(
    days: int = Query(30, ge=1, le=365),
    low_usage_threshold: int = Query(3, ge=1, le=100),
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.tool_usage_audit(
        days=days,
        low_usage_threshold=low_usage_threshold,
    )
    return {"status": "success", **data}


@router.get("/users/{user_id}/summary")
async def admin_learning_user_summary(
    user_id: UUID,
    days: int = Query(30, ge=1, le=365),
    _: UUID = Depends(get_super_admin_user),
):
    data = await LearningMetricsService.user_summary(str(user_id), days=days)
    return {"status": "success", **data}
