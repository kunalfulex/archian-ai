"""HTTP routes for Connector Metadata Agent."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.connectors.salesforce_ref import (
    enriched_salesforce_connector_ref,
    normalize_salesforce_connector_ref,
    resolve_salesforce_token,
)
from db.connection import database
from routes.auth import get_current_user
from services.connector_metadata.graph_builder import build_schema_graph
from services.connector_metadata import store
from services.connector_metadata.job_worker import start_metadata_sync_job
from services.schedule_calculator import calculate_next_run, validate_schedule_config
from utils.job_stores import metadata_sync_jobs

router = APIRouter(prefix="/api", tags=["connector-metadata"])


class SchemaSyncRequest(BaseModel):
    sync_mode: str = Field(default="schema_and_categorize")
    build_graph: bool = True
    graph_scope: str = Field(default="neighborhood")
    entity_ids: Optional[List[str]] = None


class SchemaScheduleRequest(BaseModel):
    schedule_enabled: bool = False
    schedule_frequency: Optional[str] = None
    schedule_config: Dict[str, Any] = Field(default_factory=dict)
    schedule_timezone: Optional[str] = None
    sync_mode: str = Field(default="schema_and_categorize")
    graph_scope: str = Field(default="neighborhood")


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _json_response(content: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(content, default=str)))


def _connector_ref(connector_type: str, connector_id: str) -> Dict[str, str]:
    if connector_type == "sql":
        return {"connector_id": connector_id}
    if connector_type == "google_sheets":
        return {"connection_id": connector_id}
    raise HTTPException(status_code=400, detail="Invalid connector type")


async def _assert_connector(user_id: UUID, connector_type: str, connector_id: str) -> Dict[str, str]:
    uid = str(user_id)
    if connector_type == "salesforce":
        token = await resolve_salesforce_token(uid, connector_id)
        if not token:
            raise HTTPException(status_code=404, detail="Connector not found")
        return enriched_salesforce_connector_ref(token)
    if connector_type == "sql":
        row = await database.fetch_one(
            "SELECT 1 FROM sql_connectors WHERE user_id = :u AND id = :id AND is_active = true LIMIT 1",
            {"u": uid, "id": connector_id},
        )
    elif connector_type == "google_sheets":
        row = await database.fetch_one(
            "SELECT 1 FROM google_sheets_connections WHERE user_id = :u AND id = :id LIMIT 1",
            {"u": uid, "id": connector_id},
        )
    else:
        raise HTTPException(status_code=400, detail="Invalid connector type")
    if not row:
        raise HTTPException(status_code=404, detail="Connector not found")
    return _connector_ref(connector_type, connector_id)


@router.get("/connectors/{connector_type}/{connector_id}/schema-schedule")
async def get_schema_schedule(
    connector_type: str,
    connector_id: str,
    user_id: UUID = Depends(get_current_user),
):
    ref = await _assert_connector(user_id, connector_type, connector_id)
    row = await store.get_schedule(str(user_id), connector_type, ref)
    if not row:
        return _json_response(
            {
                "schedule_enabled": False,
                "connector_type": connector_type,
                "connector_ref": ref,
            }
        )
    out = dict(row)
    for k in ("connector_ref", "schedule_config"):
        if k in out:
            out[k] = _parse_json(out[k])
    return _json_response(out)


@router.put("/connectors/{connector_type}/{connector_id}/schema-schedule")
async def put_schema_schedule(
    connector_type: str,
    connector_id: str,
    body: SchemaScheduleRequest,
    user_id: UUID = Depends(get_current_user),
):
    ref = await _assert_connector(user_id, connector_type, connector_id)
    uid = str(user_id)

    if body.schedule_enabled:
        if not body.schedule_frequency:
            raise HTTPException(status_code=400, detail="schedule_frequency required when enabled")
        ok, err = validate_schedule_config(body.schedule_frequency, body.schedule_config)
        if not ok:
            raise HTTPException(status_code=400, detail=err)

    next_run_at = None
    if body.schedule_enabled and body.schedule_frequency:
        tz = body.schedule_timezone or "UTC"
        next_run_at = calculate_next_run(
            frequency=body.schedule_frequency,
            schedule_config=body.schedule_config,
            timezone=tz,
            current_time=datetime.utcnow(),
        )

    schedule_id = await store.upsert_schedule(
        uid,
        connector_type,
        ref,
        schedule_enabled=body.schedule_enabled,
        schedule_frequency=body.schedule_frequency,
        schedule_config=body.schedule_config,
        schedule_timezone=body.schedule_timezone,
        sync_mode=body.sync_mode,
        graph_scope=body.graph_scope,
        next_run_at=next_run_at,
    )
    return _json_response({"schedule_id": schedule_id, "next_run_at": next_run_at})


@router.post("/connectors/{connector_type}/{connector_id}/schema-sync")
async def post_schema_sync(
    connector_type: str,
    connector_id: str,
    body: SchemaSyncRequest,
    user_id: UUID = Depends(get_current_user),
):
    ref = await _assert_connector(user_id, connector_type, connector_id)
    uid = str(user_id)

    active = await store.get_active_job(uid, connector_type, ref)
    if active:
        raise HTTPException(
            status_code=409,
            detail={"message": "Schema sync already in progress", "job_id": str(active["id"])},
        )

    job_id = await store.create_job(
        user_id=uid,
        connector_type=connector_type,
        connector_ref=ref,
        sync_mode=body.sync_mode,
        scope="full",
        options={
            "build_graph": body.build_graph,
            "graph_scope": body.graph_scope,
            "entity_ids": body.entity_ids,
            "trigger": "manual",
        },
    )
    await start_metadata_sync_job(job_id, uid)
    return _json_response({"job_id": job_id, "status": "queued"})


@router.get("/schema-sync/jobs/{job_id}")
async def get_schema_sync_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
):
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    redis_j = await metadata_sync_jobs.get(job_id, {})
    out = dict(job)
    for k in ("connector_ref", "options", "progress", "drift_report"):
        if k in out:
            out[k] = _parse_json(out[k])
    out["redis"] = redis_j
    return _json_response(out)


@router.get("/connectors/{connector_type}/{connector_id}/schema-graph")
async def get_schema_graph(
    connector_type: str,
    connector_id: str,
    scope: str = "neighborhood",
    include_deleted: bool = True,
    user_id: UUID = Depends(get_current_user),
):
    ref = await _assert_connector(user_id, connector_type, connector_id)
    uid = str(user_id)

    sched = await store.get_schedule(uid, connector_type, ref)
    if sched and sched.get("active_graph_id"):
        g = await store.get_graph(str(sched["active_graph_id"]), uid)
        if g:
            data = _parse_json(g.get("graph_data"))
            return _json_response(
                {
                    "graph_id": str(g["id"]),
                    "graph_scope": g.get("graph_scope"),
                    "graph": data,
                    "from_cache": True,
                }
            )

    graph = await build_schema_graph(
        user_id=uid,
        connector_type=connector_type,
        connector_ref=ref,
        graph_scope=scope,
        include_deleted=include_deleted,
    )
    return _json_response({"graph": graph.to_dict(), "from_cache": False})
