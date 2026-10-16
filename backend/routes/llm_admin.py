"""Super-admin API for LLM provider/model configuration and metrics."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from routes.auth import get_super_admin_user
from services.llm.config_store import (
    list_all_task_configs,
    list_models,
    list_providers,
    provider_has_api_key,
    update_task_config,
    _invalidate_cache,
)
from services.llm.metrics import get_metrics_summary, get_metrics_timeseries

router = APIRouter(prefix="/admin/llm", tags=["admin-llm"])


class UpdateTaskConfigRequest(BaseModel):
    provider_id: str
    model_id: str
    fallback_provider_id: Optional[str] = None
    fallback_model_id: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = Field(None, ge=1, le=128000)
    max_concurrent: Optional[int] = Field(None, ge=1, le=50)
    batch_size: Optional[int] = Field(None, ge=1, le=100)
    notes: Optional[str] = None


@router.get("/providers")
async def admin_llm_providers(_: UUID = Depends(get_super_admin_user)):
    providers = await list_providers()
    return {"status": "success", "providers": providers}


@router.get("/models")
async def admin_llm_models(
    provider_id: Optional[str] = None,
    _: UUID = Depends(get_super_admin_user),
):
    models = await list_models(provider_id)
    return {"status": "success", "models": models}


@router.get("/tasks")
async def admin_llm_tasks(_: UUID = Depends(get_super_admin_user)):
    tasks = await list_all_task_configs()
    for t in tasks:
        pid = t.get("provider_id")
        env = "OPENAI_API_KEY" if pid == "openai" else "ANTHROPIC_API_KEY" if pid == "anthropic" else ""
        t["api_key_configured"] = provider_has_api_key(env) if env else False
    return {"status": "success", "tasks": tasks}


@router.patch("/tasks/{task_key}")
async def admin_update_task(
    task_key: str,
    body: UpdateTaskConfigRequest,
    admin_id: UUID = Depends(get_super_admin_user),
):
    env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
    env = env_map.get(body.provider_id)
    if not env or not provider_has_api_key(env):
        raise HTTPException(
            status_code=400,
            detail=f"API key not configured for provider {body.provider_id}",
        )
    if body.fallback_provider_id:
        fb_env = env_map.get(body.fallback_provider_id)
        if fb_env and not provider_has_api_key(fb_env):
            raise HTTPException(
                status_code=400,
                detail=f"Fallback API key not configured for {body.fallback_provider_id}",
            )
    try:
        await update_task_config(
            task_key,
            provider_id=body.provider_id,
            model_id=body.model_id,
            fallback_provider_id=body.fallback_provider_id,
            fallback_model_id=body.fallback_model_id,
            temperature=body.temperature,
            max_tokens=body.max_tokens,
            max_concurrent=body.max_concurrent,
            batch_size=body.batch_size,
            notes=body.notes,
            updated_by=str(admin_id),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    _invalidate_cache()
    return {"status": "success", "task_key": task_key}


@router.post("/cache/invalidate")
async def admin_invalidate_llm_cache(_: UUID = Depends(get_super_admin_user)):
    _invalidate_cache()
    return {"status": "success"}


@router.get("/metrics")
async def admin_llm_metrics(
    hours: int = 24,
    task_key: Optional[str] = None,
    _: UUID = Depends(get_super_admin_user),
):
    summary = await get_metrics_summary(hours=hours, task_key=task_key)
    timeseries = await get_metrics_timeseries(hours=hours)
    return {
        "status": "success",
        "hours": hours,
        "summary": summary,
        "timeseries": timeseries,
    }
