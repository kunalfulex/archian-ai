"""HTTP routes for connector labeling and business profiles."""
from __future__ import annotations

import json
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from routes.auth import get_current_user
from services.labeling.backfill import backfill_user_labeling
from services.labeling.config import LabelingConfig
from db.connection import database
from services.labeling.connector_profile_service import get_connector_display_name
from services.labeling.connector_setup import get_connector_setup_status
from services.labeling.connector_ref import connector_instance_id
from services.labeling.profile_schedule import (
    schedule_connector_profile_rollup,
    schedule_connector_profile_rollup_fire_and_forget,
)
from services.labeling.schedule import schedule_entity_labeling
from services.connectors.salesforce_ref import (
    enriched_salesforce_connector_ref,
    resolve_salesforce_token,
)
from services.labeling.readiness_service import get_readiness_state
from services.labeling.store import LabelingStore
from utils.readiness_sse_stream import SSE_HEADERS, stream_connector_readiness_events_until_terminal

router = APIRouter(prefix="/api/labeling", tags=["labeling"])


def _json_response(content: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(content, default=str)))


async def _connector_ref_for_type(
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
) -> dict:
    if connector_type == "salesforce":
        token = await resolve_salesforce_token(user_id, connector_instance_id)
        if not token:
            raise HTTPException(status_code=404, detail="Connector not found")
        return enriched_salesforce_connector_ref(token)
    if connector_type == "sql":
        return {"connector_id": connector_instance_id}
    if connector_type == "google_sheets":
        return {"connection_id": connector_instance_id}
    raise HTTPException(status_code=400, detail=f"Invalid connector_type: {connector_type}")


async def _resolved_connector_instance_id(
    user_id: str, connector_type: str, connector_instance_id: str
) -> str:
    if connector_type == "salesforce":
        token = await resolve_salesforce_token(user_id, connector_instance_id)
        if token:
            return str(token["connector_id"])
    return connector_instance_id


class ConnectorDescriptionUpdate(BaseModel):
    user_description: str = Field(default="", max_length=8000)


class ConnectorSetupUpdate(BaseModel):
    org_display_name: Optional[str] = Field(default=None, max_length=200)
    user_description: Optional[str] = Field(default=None, max_length=8000)


class RetryFailedRequest(BaseModel):
    connector_type: Optional[str] = None
    connector_instance_id: Optional[str] = None
    entity_keys: Optional[list[str]] = None


@router.get("/status")
async def get_labeling_status(user_id: UUID = Depends(get_current_user)):
    """Operator metrics: entity counts, active jobs, and worker limits."""
    metrics = await LabelingStore.get_user_labeling_metrics(str(user_id))
    active_jobs = await LabelingStore.list_active_jobs(str(user_id))
    return _json_response(
        {
            **metrics,
            "active_jobs": active_jobs,
            "limits": {
                "max_concurrency": LabelingConfig.max_concurrency(),
                "batch_size": LabelingConfig.batch_size(),
                "refinement_debounce_sec": LabelingConfig.refinement_debounce_sec(),
            },
        }
    )


@router.get("/failed")
async def list_failed_labeling_entities(
    connector_type: Optional[str] = None,
    connector_instance_id: Optional[str] = None,
    limit: int = 100,
    user_id: UUID = Depends(get_current_user),
):
    rows = await LabelingStore.list_failed_entities(
        str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
        limit=min(max(1, limit), 500),
    )
    return _json_response({"failed_entities": rows, "count": len(rows)})


@router.post("/retry-failed")
async def retry_failed_labeling_entities(
    body: RetryFailedRequest,
    user_id: UUID = Depends(get_current_user),
):
    failed = await LabelingStore.list_failed_entities(
        str(user_id),
        connector_type=body.connector_type,
        connector_instance_id=body.connector_instance_id,
        limit=500,
    )
    if body.entity_keys:
        allowed = set(body.entity_keys)
        failed = [row for row in failed if row.get("entity_key") in allowed]
    if not failed:
        return _json_response(
            {"reset_count": 0, "queued_jobs": [], "message": "No failed entities to retry"}
        )

    reset_count = await LabelingStore.reset_failed_entities(
        str(user_id),
        connector_type=body.connector_type,
        connector_instance_id=body.connector_instance_id,
        entity_keys=[row["entity_key"] for row in failed],
    )

    by_connector: dict[tuple[str, str], list[str]] = {}
    for row in failed:
        key = (row["connector_type"], row["connector_instance_id"])
        by_connector.setdefault(key, []).append(row["entity_key"])

    queued_jobs: list[str] = []
    for (ctype, inst_id), keys in by_connector.items():
        job_id = await schedule_entity_labeling(
            user_id=str(user_id),
            connector_type=ctype,
            connector_ref=await _connector_ref_for_type(str(user_id), ctype, inst_id),
            entity_keys=keys,
        )
        if job_id:
            queued_jobs.append(job_id)

    return _json_response(
        {
            "reset_count": reset_count,
            "queued_jobs": queued_jobs,
            "message": "Failed entities reset to pending and re-queued",
        }
    )


@router.get("/jobs/{job_id}")
async def get_labeling_job(job_id: str, user_id: UUID = Depends(get_current_user)):
    job = await LabelingStore.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Labeling job not found")
    return _json_response(job)


@router.post("/backfill")
async def backfill_labeling(user_id: UUID = Depends(get_current_user)):
    """Queue labeling for all pending entities for the current user."""
    result = await backfill_user_labeling(str(user_id))
    return _json_response(result)


@router.get("/connectors/{connector_type}/{connector_instance_id}/profile")
async def get_connector_profile(
    connector_type: str,
    connector_instance_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)
    inst = await _resolved_connector_instance_id(
        str(user_id), connector_type, connector_instance_id
    )
    profile = await LabelingStore.get_business_profile(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    stats = await LabelingStore.get_entity_labeling_stats(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    setup = await get_connector_setup_status(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    return _json_response(
        {
            "connector_type": connector_type,
            "connector_instance_id": inst,
            "display_name": display_name,
            "user_description": (profile or {}).get("user_description"),
            "ai_profile": (profile or {}).get("ai_profile"),
            "profile_status": (profile or {}).get("profile_status") or "pending",
            "entity_labeling_stats": stats,
            **setup,
        }
    )


@router.patch("/connectors/{connector_type}/{connector_instance_id}/description")
async def update_connector_description(
    connector_type: str,
    connector_instance_id: str,
    body: ConnectorDescriptionUpdate,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)
    inst = await _resolved_connector_instance_id(
        str(user_id), connector_type, connector_instance_id
    )
    connector_ref = await _connector_ref_for_type(
        str(user_id), connector_type, connector_instance_id
    )
    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    await LabelingStore.upsert_business_profile_shell(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        display_name=display_name,
    )
    await LabelingStore.update_user_description(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        user_description=body.user_description or "",
    )
    schedule_connector_profile_rollup_fire_and_forget(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        force=True,
    )
    return _json_response({"ok": True, "user_description": body.user_description or ""})


@router.patch("/connectors/{connector_type}/{connector_instance_id}/setup")
async def update_connector_setup(
    connector_type: str,
    connector_instance_id: str,
    body: ConnectorSetupUpdate,
    user_id: UUID = Depends(get_current_user),
):
    """Update Salesforce org display name and/or user description (optional post-connect setup)."""
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)

    if connector_type == "salesforce" and body.org_display_name is not None:
        token = await resolve_salesforce_token(str(user_id), connector_instance_id)
        if not token:
            raise HTTPException(status_code=404, detail="Connector not found")
        name = (body.org_display_name or "").strip()
        if name:
            await database.execute(
                """
                UPDATE salesforce_tokens
                SET org_display_name = :org_display_name
                WHERE user_id = :user_id AND id::text = :connector_id
                """,
                {
                    "user_id": str(user_id),
                    "connector_id": token["connector_id"],
                    "org_display_name": name,
                },
            )

    if body.user_description is not None:
        inst = await _resolved_connector_instance_id(
            str(user_id), connector_type, connector_instance_id
        )
        connector_ref = await _connector_ref_for_type(
            str(user_id), connector_type, connector_instance_id
        )
        display_name = await get_connector_display_name(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=connector_instance_id,
        )
        await LabelingStore.upsert_business_profile_shell(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
            display_name=display_name,
        )
        await LabelingStore.update_user_description(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=inst,
            user_description=body.user_description or "",
        )
        schedule_connector_profile_rollup_fire_and_forget(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
            force=True,
        )

    setup = await get_connector_setup_status(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id,
    )
    return _json_response({"ok": True, "display_name": display_name, **setup})


@router.post("/connectors/{connector_type}/{connector_instance_id}/rollup")
async def trigger_connector_rollup(
    connector_type: str,
    connector_instance_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)
    connector_ref = await _connector_ref_for_type(
        str(user_id), connector_type, connector_instance_id
    )
    job_id = await schedule_connector_profile_rollup(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        force=True,
    )
    return _json_response({"job_id": job_id, "queued": bool(job_id)})


@router.get("/connectors/{connector_type}/{connector_instance_id}/readiness")
async def get_connector_readiness(
    connector_type: str,
    connector_instance_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)
    connector_ref = await _connector_ref_for_type(
        str(user_id), connector_type, connector_instance_id
    )
    inst = connector_ref.get("connector_id") or connector_instance_id
    if connector_type == "google_sheets":
        inst = connector_ref.get("connection_id") or connector_instance_id
    if connector_type == "salesforce":
        token = await resolve_salesforce_token(str(user_id), connector_instance_id)
        inst = token["connector_id"] if token else connector_instance_id
    state = await get_readiness_state(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        connector_ref=connector_ref,
    )
    return _json_response(state)


@router.get("/connectors/{connector_type}/{connector_instance_id}/readiness/stream")
async def stream_connector_readiness(
    connector_type: str,
    connector_instance_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_connector_ownership(str(user_id), connector_type, connector_instance_id)
    token = await resolve_salesforce_token(str(user_id), connector_instance_id)
    inst = connector_instance_id
    if connector_type == "salesforce" and token:
        inst = token["connector_id"]

    async def event_generator():
        async for line in stream_connector_readiness_events_until_terminal(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=inst,
        ):
            yield line

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


async def _assert_connector_ownership(
    user_id: str, connector_type: str, connector_instance_id: str
) -> None:
    from db.connection import database

    if connector_type == "salesforce":
        token = await resolve_salesforce_token(user_id, connector_instance_id)
        if not token:
            raise HTTPException(status_code=404, detail="Connector not found")
        return
    elif connector_type == "sql":
        row = await database.fetch_one(
            "SELECT 1 FROM sql_connectors WHERE user_id = :u AND id::text = :id LIMIT 1",
            {"u": user_id, "id": connector_instance_id},
        )
    elif connector_type == "google_sheets":
        row = await database.fetch_one(
            "SELECT 1 FROM google_sheets_connections WHERE user_id = :u AND id::text = :id LIMIT 1",
            {"u": user_id, "id": connector_instance_id},
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid connector_type")
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
