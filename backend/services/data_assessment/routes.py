"""HTTP routes for connector data assessment."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from db.connection import database
from routes.auth import get_current_user
from services.data_assessment import store
from services.data_assessment.api_serializers import serialize_job
from services.data_assessment.job_worker import resume_assessment_job, start_assessment_job
from services.data_assessment.pdf_export import build_assessment_pdf, pdf_download_filename
from services.connectors.salesforce_ref import (
    enriched_salesforce_connector_ref,
    resolve_salesforce_token,
)
from utils.assessment_sse_stream import (
    SSE_HEADERS,
    stream_assessment_job_events_until_terminal,
)
from utils.job_stores import assessment_jobs

router = APIRouter(prefix="/api", tags=["data-assessment"])


class StartAssessmentRequest(BaseModel):
    scope: str = Field(default="standard", description="quick | standard | full")
    entities: Optional[List[str]] = None
    options: Dict[str, Any] = Field(default_factory=dict)


async def _reject_if_active_assessment(
    user_id: UUID,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> None:
    active = await store.get_active_job_for_connector(
        str(user_id), connector_type, connector_ref
    )
    if not active:
        return
    raise HTTPException(
        status_code=409,
        detail={
            "message": "An assessment is already in progress for this connector.",
            "job_id": str(active["id"]),
            "status": active.get("status"),
        },
    )


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def _json_response(content: Any) -> JSONResponse:
    """JSONResponse does not accept json.dumps default=; encode non-JSON types safely."""
    return JSONResponse(content=json.loads(json.dumps(content, default=str)))


def _json_scalar(val: Any) -> Any:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    return val


def _serialize_report_summary(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: _json_scalar(v) for k, v in dict(row).items()}
    for key in ("connector_ref", "scores", "sections"):
        if key in out and out[key] is not None:
            out[key] = _parse_json(out[key])
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    return out


def _entity_option(value: str, label: Optional[str] = None) -> Dict[str, str]:
    display = (label or "").strip()
    if display:
        return {"value": value, "label": display}
    return {"value": value, "label": value}


async def _list_salesforce_entities(user_id: str, org_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT object_api_name, label
        FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY COALESCE(label, object_api_name), object_api_name
        """,
        {"user_id": user_id, "org_id": org_id},
    )
    out: List[Dict[str, str]] = []
    for r in rows or []:
        row = dict(r)
        api_name = row.get("object_api_name")
        if api_name:
            out.append(_entity_option(api_name, row.get("label")))
    return out


async def _list_sql_entities(user_id: str, connector_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT DISTINCT table_name
        FROM sql_extracted_schemas
        WHERE connector_id = :connector_id AND user_id = :user_id
        ORDER BY table_name
        """,
        {"connector_id": connector_id, "user_id": user_id},
    )
    out: List[Dict[str, str]] = []
    for r in rows or []:
        table = dict(r).get("table_name")
        if table:
            out.append(_entity_option(table))
    return out


async def _list_google_sheets_entities(user_id: str, connection_id: str) -> List[Dict[str, str]]:
    rows = await database.fetch_all(
        """
        SELECT spreadsheet_id, spreadsheet_name, sheet_name
        FROM user_google_spreadsheets
        WHERE user_id = :user_id AND google_sheets_connection_id = :conn_id
        ORDER BY spreadsheet_name, sheet_name
        """,
        {"user_id": user_id, "conn_id": connection_id},
    )
    out: List[Dict[str, str]] = []
    for r in rows or []:
        row = dict(r)
        sid = row.get("spreadsheet_id")
        sheet = row.get("sheet_name")
        if not sid or not sheet:
            continue
        value = f"{sid}::{sheet}"
        sp_name = row.get("spreadsheet_name") or sid
        out.append(_entity_option(value, f"{sp_name} / {sheet}"))
    return out


async def _resolve_salesforce_connector_ref(
    user_id: UUID,
    connector_id: str,
) -> Dict[str, str]:
    token = await resolve_salesforce_token(str(user_id), connector_id)
    if not token:
        raise HTTPException(status_code=404, detail="Salesforce org not found")
    return enriched_salesforce_connector_ref(token)


def _salesforce_active_ref(connector_ref: Dict[str, str]) -> Dict[str, str]:
    return {"connector_id": connector_ref["connector_id"]}


async def _assert_sql_connector(user_id: UUID, connector_id: str) -> None:
    row = await database.fetch_one(
        """
        SELECT 1 FROM sql_connectors
        WHERE id = :id AND user_id = :user_id AND is_active = true LIMIT 1
        """,
        {"id": connector_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="SQL connector not found")


async def _assert_gs_connection(user_id: UUID, conn_id: str) -> None:
    row = await database.fetch_one(
        """
        SELECT 1 FROM google_sheets_connections
        WHERE id = :id AND user_id = :user_id LIMIT 1
        """,
        {"id": conn_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Google Sheets connection not found")


@router.post("/connectors/salesforce/{connector_id}/assessments")
async def start_salesforce_assessment(
    connector_id: str,
    body: StartAssessmentRequest,
    user_id: UUID = Depends(get_current_user),
):
    connector_ref = await _resolve_salesforce_connector_ref(user_id, connector_id)
    await _reject_if_active_assessment(
        user_id, "salesforce", _salesforce_active_ref(connector_ref)
    )
    job_id = await store.create_job(
        user_id=str(user_id),
        connector_type="salesforce",
        connector_ref=connector_ref,
        scope=body.scope,
        entity_list=body.entities,
        options=body.options,
    )
    await start_assessment_job(job_id, str(user_id))
    return {"status": "ok", "job_id": job_id}


@router.post("/connectors/sql/{connector_id}/assessments")
async def start_sql_assessment(
    connector_id: str,
    body: StartAssessmentRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_sql_connector(user_id, connector_id)
    connector_ref = {"connector_id": connector_id}
    await _reject_if_active_assessment(user_id, "sql", connector_ref)
    job_id = await store.create_job(
        user_id=str(user_id),
        connector_type="sql",
        connector_ref=connector_ref,
        scope=body.scope,
        entity_list=body.entities,
        options=body.options,
    )
    await start_assessment_job(job_id, str(user_id))
    return {"status": "ok", "job_id": job_id}


@router.post("/connectors/google-sheets/{connection_id}/assessments")
async def start_sheets_assessment(
    connection_id: str,
    body: StartAssessmentRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _assert_gs_connection(user_id, connection_id)
    connector_ref = {"google_sheets_connection_id": connection_id}
    await _reject_if_active_assessment(user_id, "google_sheets", connector_ref)
    job_id = await store.create_job(
        user_id=str(user_id),
        connector_type="google_sheets",
        connector_ref=connector_ref,
        scope=body.scope,
        entity_list=body.entities,
        options=body.options,
    )
    await start_assessment_job(job_id, str(user_id))
    return {"status": "ok", "job_id": job_id}


@router.get("/assessments/{job_id}")
async def get_assessment_job(job_id: str, user_id: UUID = Depends(get_current_user)):
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    redis_j = await assessment_jobs.get(job_id, {})
    serialized = serialize_job(job)
    return _json_response({"job": {**serialized, "redis": redis_j}})


@router.get("/assessments/{job_id}/stream")
async def stream_assessment_job(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """SSE progress for a connector assessment job (replaces client polling)."""
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Use the async iterator directly — a nested generator causes
    # "aclose(): asynchronous generator is already running" on client disconnect.
    return StreamingResponse(
        stream_assessment_job_events_until_terminal(job_id, str(user_id)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/assessments/{job_id}/pause")
async def pause_assessment(job_id: str, user_id: UUID = Depends(get_current_user)):
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await store.update_job(job_id, str(user_id), status="paused")
    await assessment_jobs.update(job_id, {"status": "paused"})
    return {"status": "paused"}


@router.post("/assessments/{job_id}/resume")
async def resume_assessment(job_id: str, user_id: UUID = Depends(get_current_user)):
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await resume_assessment_job(job_id, str(user_id))
    return {"status": "resuming"}


@router.post("/assessments/{job_id}/cancel")
async def cancel_assessment(job_id: str, user_id: UUID = Depends(get_current_user)):
    job = await store.get_job(job_id, str(user_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    await store.update_job(job_id, str(user_id), status="cancelled")
    await assessment_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})
    return {"status": "cancelled"}


@router.get("/assessments/reports/{report_id}")
async def get_assessment_report(report_id: str, user_id: UUID = Depends(get_current_user)):
    report = await store.get_report(report_id, str(user_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    out = _serialize_report_summary(dict(report))
    out["entities"] = _parse_json(report.get("entities"))
    return _json_response({"report": out})


@router.get("/assessments/reports/{report_id}/pdf")
async def download_assessment_report_pdf(
    report_id: str,
    user_id: UUID = Depends(get_current_user),
    connector_display_name: Optional[str] = Query(default=None, max_length=200),
):
    """Download a client-shareable PDF for an assessment report."""
    report = await store.get_report(report_id, str(user_id))
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    report_dict = dict(report)
    connector_type = report_dict.get("connector_type") or "salesforce"
    pdf_bytes = build_assessment_pdf(
        report_dict,
        connector_type=connector_type,
        connector_display_name=connector_display_name,
    )
    filename = pdf_download_filename(
        report_dict,
        connector_display_name=connector_display_name,
    )
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _active_job_payload(job: Dict[str, Any]) -> Dict[str, Any]:
    job_id = str(job["id"])
    redis_j = await assessment_jobs.get(job_id, {})
    serialized = serialize_job(job)
    return {"job": {**serialized, "id": job_id, "redis": redis_j}}


@router.get("/connectors/salesforce/{connector_id}/assessments/active-job")
async def get_sf_active_assessment_job(
    connector_id: str, user_id: UUID = Depends(get_current_user)
):
    connector_ref = await _resolve_salesforce_connector_ref(user_id, connector_id)
    job = await store.get_active_job_for_connector(
        str(user_id), "salesforce", _salesforce_active_ref(connector_ref)
    )
    if not job:
        return {"job": None}
    return await _active_job_payload(job)


@router.get("/connectors/sql/{connector_id}/assessments/active-job")
async def get_sql_active_assessment_job(
    connector_id: str, user_id: UUID = Depends(get_current_user)
):
    await _assert_sql_connector(user_id, connector_id)
    job = await store.get_active_job_for_connector(
        str(user_id), "sql", {"connector_id": connector_id}
    )
    if not job:
        return {"job": None}
    return await _active_job_payload(job)


@router.get("/connectors/google-sheets/{connection_id}/assessments/active-job")
async def get_sheets_active_assessment_job(
    connection_id: str, user_id: UUID = Depends(get_current_user)
):
    await _assert_gs_connection(user_id, connection_id)
    job = await store.get_active_job_for_connector(
        str(user_id),
        "google_sheets",
        {"google_sheets_connection_id": connection_id},
    )
    if not job:
        return {"job": None}
    return await _active_job_payload(job)


@router.get("/connectors/salesforce/{connector_id}/assessments/entities")
async def list_sf_assessment_entities(
    connector_id: str, user_id: UUID = Depends(get_current_user)
):
    """Assessable entities from synced connector metadata (DB only, no Salesforce API)."""
    connector_ref = await _resolve_salesforce_connector_ref(user_id, connector_id)
    return {
        "entities": await _list_salesforce_entities(
            str(user_id), connector_ref["org_id"]
        )
    }


@router.get("/connectors/sql/{connector_id}/assessments/entities")
async def list_sql_assessment_entities(
    connector_id: str, user_id: UUID = Depends(get_current_user)
):
    await _assert_sql_connector(user_id, connector_id)
    return {"entities": await _list_sql_entities(str(user_id), connector_id)}


@router.get("/connectors/google-sheets/{connection_id}/assessments/entities")
async def list_sheets_assessment_entities(
    connection_id: str, user_id: UUID = Depends(get_current_user)
):
    await _assert_gs_connection(user_id, connection_id)
    return {"entities": await _list_google_sheets_entities(str(user_id), connection_id)}


@router.get("/connectors/salesforce/{connector_id}/assessments/reports")
async def list_sf_reports(connector_id: str, user_id: UUID = Depends(get_current_user)):
    connector_ref = await _resolve_salesforce_connector_ref(user_id, connector_id)
    reports = await store.list_reports(
        str(user_id), "salesforce", _salesforce_active_ref(connector_ref)
    )
    return {"reports": [_serialize_report_summary(r) for r in reports]}


@router.get("/connectors/sql/{connector_id}/assessments/reports")
async def list_sql_reports(connector_id: str, user_id: UUID = Depends(get_current_user)):
    await _assert_sql_connector(user_id, connector_id)
    reports = await store.list_reports(str(user_id), "sql", {"connector_id": connector_id})
    return {"reports": [_serialize_report_summary(r) for r in reports]}


@router.get("/connectors/google-sheets/{connection_id}/assessments/reports")
async def list_sheets_reports(connection_id: str, user_id: UUID = Depends(get_current_user)):
    await _assert_gs_connection(user_id, connection_id)
    reports = await store.list_reports(
        str(user_id), "google_sheets", {"google_sheets_connection_id": connection_id}
    )
    return {"reports": [_serialize_report_summary(r) for r in reports]}
