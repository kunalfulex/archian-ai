"""HTTP routes for Enterprise Graph."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from db.connection import database
from routes.auth import get_current_user
from services.enterprise_graph import store
from services.enterprise_graph.config import (
    GRAPH_STATUSES,
    RUN_DEPTHS,
    max_members_per_graph,
)
from services.enterprise_graph.job_worker import start_graph_run
from services.enterprise_graph.run_controls import (
    cancel_member_assessment_jobs,
    pause_member_assessment_jobs,
    resume_member_assessment_jobs,
)
from services.enterprise_graph.run_progress import enrich_run_with_live_progress
from utils.assessment_sse_stream import (
    SSE_HEADERS,
    stream_enterprise_graph_run_events_until_terminal,
)
from services.enterprise_graph.linking import (
    load_member_results_from_assessments,
    refresh_latest_report_snapshot,
)
from services.enterprise_graph.inventory import build_inventory_from_member_results
from services.enterprise_graph.report_builder import snapshot_for_assessment_dashboard
from services.enterprise_graph.validation import validate_member_list_for_run
from utils.job_stores import enterprise_graph_jobs
from services.connectors.salesforce_ref import (
    enriched_salesforce_connector_ref,
    resolve_salesforce_token,
)
from services.enterprise_graph.validation import (
    assert_connector_owned,
    check_duplicate_members,
    connector_id_from_ref,
    connector_ref_for_type,
    resolve_display_name,
    validate_connector_type,
    validate_member_list_size,
    validate_member_role,
)
from services.sql_connector_service import SQLConnectorService

router = APIRouter(prefix="/api/enterprise-graphs", tags=["enterprise-graph"])
logger = logging.getLogger(__name__)


async def _refresh_snapshot_after_mapping_change(graph_id: str, user_id: str) -> None:
    try:
        await refresh_latest_report_snapshot(
            graph_id, user_id, rebuild_warehouse=False
        )
    except Exception:
        logger.exception(
            "Background snapshot refresh failed for graph %s", graph_id
        )


class CreateGraphRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    settings: Dict[str, Any] = Field(default_factory=dict)


class UpdateGraphRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class MemberPayload(BaseModel):
    connector_type: str
    connector_id: str
    role: str = "source"
    display_name: Optional[str] = None
    sort_order: int = 0
    sheet_entity_allowlist: Optional[List[str]] = Field(
        default=None,
        description="Google Sheets entity ids (spreadsheet_id::sheet_name); null=all, []=none",
    )


class ReplaceMembersRequest(BaseModel):
    members: List[MemberPayload] = Field(default_factory=list)


class StartRunRequest(BaseModel):
    depth: str = Field(default="partial", description="quick | partial | full")
    run_type: str = Field(
        default="assessment",
        description="assessment | remap | warehouse_redesign | lake_redesign",
    )
    reopen_for_review: bool = Field(
        default=False,
        description="When true with remap, move confirmed mappings back to suggested before re-linking",
    )


class MappingEntityUpdate(BaseModel):
    source_member_id: str
    source_entity_id: str


class UpdateMappingRequest(BaseModel):
    status: Optional[str] = Field(
        default=None, description="confirmed | rejected | suggested"
    )
    entities: Optional[List[MappingEntityUpdate]] = Field(
        default=None,
        description="Per-member entity picks for this canonical cluster",
    )


def _json_response(content: Any) -> JSONResponse:
    return JSONResponse(content=json.loads(json.dumps(content, default=str)))


async def _get_graph_or_404(graph_id: str, user_id: UUID):
    graph = await store.get_graph(graph_id, str(user_id))
    if not graph:
        raise HTTPException(status_code=404, detail="Enterprise graph not found")
    return graph


async def _member_payloads_to_rows(
    user_id: UUID,
    payloads: List[MemberPayload],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in payloads:
        ct = validate_connector_type(p.connector_type)
        await assert_connector_owned(user_id, ct, p.connector_id.strip())
        ref = connector_ref_for_type(ct, p.connector_id.strip())
        if ct == "salesforce":
            token = await resolve_salesforce_token(str(user_id), p.connector_id.strip())
            if token:
                ref = enriched_salesforce_connector_ref(token)
        role = validate_member_role(p.role)
        display = await resolve_display_name(user_id, ct, p.connector_id.strip(), p.display_name)
        if ct == "google_sheets" and p.sheet_entity_allowlist is not None:
            ref["sheet_entity_allowlist"] = [
                s.strip() for s in p.sheet_entity_allowlist if s and s.strip()
            ]
        rows.append(
            {
                "connector_type": ct,
                "connector_ref": ref,
                "role": role,
                "display_name": display,
                "sort_order": p.sort_order,
            }
        )
    check_duplicate_members(rows)
    from services.enterprise_graph.validation import validate_lake_member_constraints

    await validate_lake_member_constraints(str(user_id), rows)
    return rows


@router.get("")
async def list_enterprise_graphs(
    include_archived: bool = False,
    user_id: UUID = Depends(get_current_user),
):
    graphs = await store.list_graphs(str(user_id), include_archived=include_archived)
    return _json_response({"status": "success", "graphs": graphs})


@router.post("")
async def create_enterprise_graph(
    body: CreateGraphRequest,
    user_id: UUID = Depends(get_current_user),
):
    graph_id = await store.create_graph(
        user_id=str(user_id),
        name=body.name,
        description=body.description,
        settings=body.settings,
    )
    graph = await store.get_graph(graph_id, str(user_id))
    return _json_response({"status": "success", "graph": graph.to_dict() if graph else {"id": graph_id}})


@router.get("/runs/{run_id}")
async def get_enterprise_graph_run(
    run_id: str,
    user_id: UUID = Depends(get_current_user),
):
    run = await store.get_run(run_id, str(user_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    run = await enrich_run_with_live_progress(run, str(user_id))
    redis_j = await enterprise_graph_jobs.get(run_id, {})
    return _json_response({"status": "success", "run": {**run, "redis": redis_j}})


@router.get("/runs/{run_id}/stream")
async def stream_enterprise_graph_run(
    run_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """SSE progress for an enterprise graph run (replaces client polling)."""
    run = await store.get_run(run_id, str(user_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    return StreamingResponse(
        stream_enterprise_graph_run_events_until_terminal(run_id, str(user_id)),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("/{graph_id}/runs/active")
async def get_active_enterprise_graph_run(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Return in-progress run for this graph so the UI can resume polling after reload."""
    await _get_graph_or_404(graph_id, user_id)
    run = await store.get_active_run(graph_id, str(user_id))
    if not run:
        return _json_response({"status": "success", "run": None})
    run = await enrich_run_with_live_progress(run, str(user_id))
    redis_j = await enterprise_graph_jobs.get(run["id"], {})
    return _json_response({"status": "success", "run": {**run, "redis": redis_j}})


@router.post("/runs/{run_id}/cancel")
async def cancel_enterprise_graph_run(
    run_id: str,
    user_id: UUID = Depends(get_current_user),
):
    run = await store.get_run(run_id, str(user_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") in ("completed", "failed", "cancelled"):
        return _json_response({"status": "success", "message": "Run already finished"})
    member_jobs = list(run.get("member_jobs") or [])
    await cancel_member_assessment_jobs(member_jobs, str(user_id))
    await store.update_run(
        run_id,
        str(user_id),
        status="cancelled",
        error_summary="Cancelled by user",
        member_jobs=member_jobs,
    )
    await enterprise_graph_jobs.update(run_id, {"cancelled": True, "status": "cancelled"})
    return _json_response({"status": "success", "run_status": "cancelled"})


@router.post("/runs/{run_id}/pause")
async def pause_enterprise_graph_run(
    run_id: str,
    user_id: UUID = Depends(get_current_user),
):
    run = await store.get_run(run_id, str(user_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") not in ("queued", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot pause run with status {run.get('status')}",
        )
    member_jobs = list(run.get("member_jobs") or [])
    await pause_member_assessment_jobs(member_jobs, str(user_id))
    progress = dict(run.get("progress") or {})
    progress["message"] = "Paused"
    await store.update_run(
        run_id,
        str(user_id),
        status="paused",
        member_jobs=member_jobs,
        progress=progress,
    )
    await enterprise_graph_jobs.update(run_id, {"status": "paused"})
    return _json_response({"status": "success", "run_status": "paused"})


@router.post("/runs/{run_id}/resume")
async def resume_enterprise_graph_run(
    run_id: str,
    user_id: UUID = Depends(get_current_user),
):
    run = await store.get_run(run_id, str(user_id))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.get("status") != "paused":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot resume run with status {run.get('status')}",
        )
    member_jobs = list(run.get("member_jobs") or [])
    await resume_member_assessment_jobs(member_jobs, str(user_id))
    progress = dict(run.get("progress") or {})
    if progress.get("message") == "Paused":
        progress["message"] = "Resuming assessment…"
    await store.update_run(
        run_id,
        str(user_id),
        status="running",
        member_jobs=member_jobs,
        progress=progress,
    )
    await enterprise_graph_jobs.update(run_id, {"status": "running", "cancelled": False})
    return _json_response({"status": "success", "run_status": "running"})


@router.get("/{graph_id}")
async def get_enterprise_graph(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    return _json_response({"status": "success", "graph": graph.to_dict()})


@router.patch("/{graph_id}")
async def update_enterprise_graph(
    graph_id: str,
    body: UpdateGraphRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    if body.status is not None and body.status not in GRAPH_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of: {', '.join(sorted(GRAPH_STATUSES))}",
        )
    await store.update_graph(
        graph_id,
        str(user_id),
        name=body.name,
        description=body.description,
        status=body.status,
        settings=body.settings,
    )
    graph = await store.get_graph(graph_id, str(user_id))
    return _json_response({"status": "success", "graph": graph.to_dict() if graph else {}})


@router.delete("/{graph_id}")
async def archive_enterprise_graph(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    await store.archive_graph(graph_id, str(user_id))
    return _json_response({"status": "success", "message": "Graph archived"})


@router.put("/{graph_id}/members")
async def replace_enterprise_graph_members(
    graph_id: str,
    body: ReplaceMembersRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    validate_member_list_size(body.members)
    rows = await _member_payloads_to_rows(user_id, body.members)
    members = await store.replace_members(graph_id, str(user_id), rows)
    return _json_response(
        {
            "status": "success",
            "members": [m.to_dict() for m in members],
        }
    )


@router.post("/{graph_id}/members")
async def add_enterprise_graph_member(
    graph_id: str,
    body: MemberPayload,
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    if len(graph.members) >= max_members_per_graph():
        raise HTTPException(
            status_code=400,
            detail=f"A graph may have at most {max_members_per_graph()} connectors.",
        )
    rows = await _member_payloads_to_rows(user_id, [body])
    existing_keys = {
        (
            m.connector_type,
            json.dumps(m.connector_ref, sort_keys=True),
        )
        for m in graph.members
    }
    new_key = (rows[0]["connector_type"], json.dumps(rows[0]["connector_ref"], sort_keys=True))
    if new_key in existing_keys:
        raise HTTPException(status_code=400, detail="Connector already in this graph.")
    sort_order = body.sort_order if body.sort_order else len(graph.members)
    rows[0]["sort_order"] = sort_order
    member_id = await store.add_member(graph_id, rows[0])
    member = await store.get_member(graph_id, member_id)
    return _json_response({"status": "success", "member": member.to_dict() if member else {"id": member_id}})


@router.delete("/{graph_id}/members/{member_id}")
async def remove_enterprise_graph_member(
    graph_id: str,
    member_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    member = await store.get_member(graph_id, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Graph member not found")
    await store.remove_member(graph_id, member_id, user_id=str(user_id))
    return _json_response({"status": "success"})


@router.post("/{graph_id}/runs")
async def start_enterprise_graph_run(
    graph_id: str,
    body: StartRunRequest,
    user_id: UUID = Depends(get_current_user),
):
    depth = (body.depth or "partial").strip().lower()
    if depth not in RUN_DEPTHS:
        raise HTTPException(
            status_code=400,
            detail=f"depth must be one of: {', '.join(sorted(RUN_DEPTHS))}",
        )
    run_type = (body.run_type or "assessment").strip().lower()
    if run_type not in ("assessment", "remap", "warehouse_redesign", "lake_redesign"):
        raise HTTPException(
            status_code=400,
            detail="run_type must be assessment, remap, warehouse_redesign, or lake_redesign",
        )

    graph = await _get_graph_or_404(graph_id, user_id)
    validate_member_list_for_run(graph.members)
    active = await store.get_active_run(graph_id, str(user_id))
    if active:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "An assessment run is already in progress for this graph.",
                "run_id": active["id"],
            },
        )
    if run_type == "remap" and body.reopen_for_review:
        await store.reopen_confirmed_mappings(graph_id)

    run_id = await store.create_run(
        graph_id=graph_id,
        user_id=str(user_id),
        depth=depth,
        run_type=run_type,
    )
    await start_graph_run(run_id, str(user_id))
    return _json_response(
        {
            "status": "success",
            "run_id": run_id,
            "run_type": run_type,
            "reopen_for_review": bool(body.reopen_for_review),
        }
    )


@router.get("/{graph_id}/report/latest")
async def get_latest_enterprise_graph_report(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    run = await store.get_latest_completed_run(graph_id, str(user_id))
    if not run or not run.get("report_snapshot"):
        raise HTTPException(status_code=404, detail="No completed assessment report for this graph")
    snapshot = run["report_snapshot"]
    return _json_response(
        {
            "status": "success",
            "run_id": run["id"],
            "report": snapshot_for_assessment_dashboard(snapshot),
            "report_snapshot": snapshot,
        }
    )


MAPPING_STATUSES = frozenset({"suggested", "confirmed", "rejected"})


@router.get("/{graph_id}/mappings")
async def list_enterprise_graph_mappings(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    rows = await store.list_semantic_mappings(graph_id)
    results = await load_member_results_from_assessments(str(user_id), graph.members)
    inventory = await build_inventory_from_member_results(
        graph.members, results, user_id=str(user_id)
    )
    from services.enterprise_graph.entity_resolution import entity_label_for

    enriched = []
    for row in rows:
        r = dict(row)
        mid = str(r.get("source_member_id") or "")
        eid = str(r.get("source_entity_id") or "")
        r["source_entity_label"] = entity_label_for(mid, eid, inventory)
        enriched.append(r)
    return _json_response({"status": "success", "mappings": enriched})


@router.get("/{graph_id}/member-entities")
async def list_graph_member_entities(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Assessed entities per graph member (for mapping edit dropdowns)."""
    graph = await _get_graph_or_404(graph_id, user_id)
    results = await load_member_results_from_assessments(str(user_id), graph.members)
    inventory = await build_inventory_from_member_results(
        graph.members, results, user_id=str(user_id)
    )
    member_names = {m.id: m.display_name or m.connector_type for m in graph.members}
    by_member: Dict[str, Dict[str, Any]] = {}
    for ent in inventory:
        mid = ent.member_id
        if mid not in by_member:
            by_member[mid] = {
                "member_id": mid,
                "display_name": member_names.get(mid, mid),
                "entities": [],
            }
        by_member[mid]["entities"].append(
            {"entity_id": ent.entity_id, "label": ent.label or ent.entity_id}
        )
    for payload in by_member.values():
        payload["entities"].sort(key=lambda e: (e.get("label") or "").lower())
    return _json_response(
        {
            "status": "success",
            "members": sorted(by_member.values(), key=lambda m: m.get("display_name") or ""),
        }
    )


@router.post("/{graph_id}/mappings/reopen")
async def reopen_enterprise_graph_mappings(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Move all confirmed mappings to suggested so they appear for review again."""
    await _get_graph_or_404(graph_id, user_id)
    count = await store.reopen_confirmed_mappings(graph_id)
    mappings = await store.list_semantic_mappings(graph_id)
    asyncio.create_task(
        _refresh_snapshot_after_mapping_change(graph_id, str(user_id))
    )
    return _json_response(
        {"status": "success", "mappings": mappings, "reopened_count": count}
    )


@router.patch("/{graph_id}/mappings/{mapping_id}")
async def update_enterprise_graph_mapping(
    graph_id: str,
    mapping_id: str,
    body: UpdateMappingRequest,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    mapping = await store.get_semantic_mapping(graph_id, mapping_id)
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    canonical_id = mapping.get("canonical_entity_id")

    if body.entities and canonical_id:
        try:
            await store.update_canonical_mapping_entities(
                graph_id,
                str(canonical_id),
                [e.model_dump() for e in body.entities],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if body.status:
        status = body.status.strip().lower()
        if status not in MAPPING_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"status must be one of: {', '.join(sorted(MAPPING_STATUSES))}",
            )
        if canonical_id:
            await store.update_mappings_status_by_canonical(graph_id, canonical_id, status)
        else:
            await store.update_mapping_status(graph_id, mapping_id, status)

    if not body.status and not body.entities:
        raise HTTPException(
            status_code=400,
            detail="Provide status and/or entities to update",
        )

    mappings = await store.list_semantic_mappings(graph_id)
    asyncio.create_task(
        _refresh_snapshot_after_mapping_change(graph_id, str(user_id))
    )
    return _json_response({"status": "success", "mappings": mappings})


@router.get("/{graph_id}/lake-proposals")
async def list_lake_proposals(
    graph_id: str,
    summary: bool = False,
    limit: int = 20,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    if summary:
        proposals = await store.list_lake_proposal_summaries(graph_id, limit=limit)
    else:
        proposals = await store.list_lake_proposals(graph_id, limit=limit)
    return _json_response({"status": "success", "proposals": proposals})


@router.get("/{graph_id}/lake-proposals/latest")
async def get_latest_lake_proposal(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_latest_lake_proposal(graph_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="No lake proposal for this graph")
    return _json_response({"status": "success", "proposal": proposal})


@router.get("/{graph_id}/lake-proposals/{proposal_id}")
async def get_lake_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_lake_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Lake proposal not found")
    return _json_response({"status": "success", "proposal": proposal})


@router.post("/{graph_id}/lake-proposals/{proposal_id}/approve")
async def approve_lake_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_lake_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Lake proposal not found")
    approved = await store.approve_lake_proposal(graph_id, proposal_id)
    return _json_response(
        {
            "status": "success",
            "message": "Proposal approved (no DDL executed)",
            "proposal": approved,
        }
    )


@router.post("/{graph_id}/lake-proposals/{proposal_id}/execute")
async def execute_lake_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_lake_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Lake proposal not found")
    ddl_text = (proposal.get("ddl_text") or "").strip()
    if not ddl_text:
        raise HTTPException(status_code=400, detail="No DDL text in this proposal")
    if (proposal.get("dialect") or "").strip().lower() != "snowflake":
        raise HTTPException(status_code=400, detail="DDL execution is currently supported only for Snowflake proposals")

    lake_member = next((m for m in graph.members if (m.role or "").lower() == "lake"), None)
    if not lake_member:
        raise HTTPException(status_code=400, detail="No lake member configured on this graph")
    if (lake_member.connector_type or "").lower() != "sql":
        raise HTTPException(status_code=400, detail="Lake member must be a SQL connector")

    connector_id = connector_id_from_ref(lake_member.connector_type, lake_member.connector_ref)
    if not connector_id:
        raise HTTPException(status_code=400, detail="Invalid lake connector reference")
    await assert_connector_owned(user_id, "sql", connector_id)

    row = await database.fetch_one(
        """
        SELECT * FROM sql_connectors
        WHERE id = :id AND user_id = :user_id AND is_active = true
        LIMIT 1
        """,
        {"id": connector_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Lake SQL connector not found")
    connector_row = dict(row)
    if (connector_row.get("connector_type") or "").strip().lower() != "snowflake":
        raise HTTPException(status_code=400, detail="Lake SQL connector must be Snowflake")

    from services.enterprise_graph.lake_designer import (
        normalize_lake_proposal_ddl,
        resolve_lake_schemas,
    )
    from services.sql_connector_service import SQLConnectorService, split_sql_statements

    connector_service = SQLConnectorService()
    connector_config = connector_service.prepare_connector_config(connector_row)
    bronze_schema, silver_schema = resolve_lake_schemas(
        graph.settings or {},
        graph.members,
        connector_config=connector_config,
    )
    ddl_text = normalize_lake_proposal_ddl(ddl_text, bronze_schema, silver_schema)
    connector = connector_service.create_connector_instance("snowflake", connector_config)
    try:
        statements = split_sql_statements(ddl_text)
        if not statements:
            raise HTTPException(status_code=400, detail="No executable SQL statements found in DDL")
        for stmt in statements:
            await connector.execute_non_query(stmt)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to execute DDL: {str(e)}")
    finally:
        try:
            await connector.close_connection()
        except Exception:
            pass

    await store.mark_lake_proposal_ddl_executed(graph_id, proposal_id)

    return _json_response(
        {
            "status": "success",
            "message": "DDL executed successfully",
            "executed_statements": len(statements),
            "proposal_id": proposal_id,
        }
    )


@router.get("/{graph_id}/warehouse-proposals")
async def list_warehouse_proposals(
    graph_id: str,
    summary: bool = False,
    limit: int = 20,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    if summary:
        proposals = await store.list_warehouse_proposal_summaries(graph_id, limit=limit)
    else:
        proposals = await store.list_warehouse_proposals(graph_id, limit=limit)
    return _json_response({"status": "success", "proposals": proposals})


@router.get("/{graph_id}/warehouse-proposals/latest")
async def get_latest_warehouse_proposal(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_latest_warehouse_proposal(graph_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="No warehouse proposal for this graph")
    return _json_response({"status": "success", "proposal": proposal})


@router.get("/{graph_id}/warehouse-proposals/{proposal_id}")
async def get_warehouse_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_warehouse_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Warehouse proposal not found")
    return _json_response({"status": "success", "proposal": proposal})


@router.post("/{graph_id}/warehouse-proposals/{proposal_id}/approve")
async def approve_warehouse_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_warehouse_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Warehouse proposal not found")
    approved = await store.approve_warehouse_proposal(graph_id, proposal_id)
    return _json_response(
        {
            "status": "success",
            "message": "Proposal approved (no DDL executed)",
            "proposal": approved,
        }
    )


@router.post("/{graph_id}/warehouse-proposals/{proposal_id}/execute")
async def execute_warehouse_proposal(
    graph_id: str,
    proposal_id: str,
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    proposal = await store.get_warehouse_proposal(graph_id, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail="Warehouse proposal not found")
    ddl_text = (proposal.get("ddl_text") or "").strip()
    if not ddl_text:
        raise HTTPException(status_code=400, detail="No DDL text in this proposal")
    if (proposal.get("dialect") or "").strip().lower() != "snowflake":
        raise HTTPException(status_code=400, detail="DDL execution is currently supported only for Snowflake proposals")

    warehouse_member = next((m for m in graph.members if (m.role or "").lower() == "warehouse"), None)
    if not warehouse_member:
        raise HTTPException(status_code=400, detail="No warehouse member configured on this graph")
    if (warehouse_member.connector_type or "").lower() != "sql":
        raise HTTPException(status_code=400, detail="Warehouse member must be a SQL connector")

    connector_id = connector_id_from_ref(warehouse_member.connector_type, warehouse_member.connector_ref)
    if not connector_id:
        raise HTTPException(status_code=400, detail="Invalid warehouse connector reference")
    await assert_connector_owned(user_id, "sql", connector_id)

    row = await database.fetch_one(
        """
        SELECT * FROM sql_connectors
        WHERE id = :id AND user_id = :user_id AND is_active = true
        LIMIT 1
        """,
        {"id": connector_id, "user_id": str(user_id)},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Warehouse SQL connector not found")
    connector_row = dict(row)
    if (connector_row.get("connector_type") or "").strip().lower() != "snowflake":
        raise HTTPException(status_code=400, detail="Warehouse SQL connector must be Snowflake")

    from services.enterprise_graph.warehouse_designer import (
        normalize_snowflake_proposal_ddl,
        resolve_warehouse_target_dataset,
    )

    connector_service = SQLConnectorService()
    connector_config = connector_service.prepare_connector_config(connector_row)
    target_schema = resolve_warehouse_target_dataset(
        graph.settings or {},
        graph.members,
        "snowflake",
        connector_config=connector_config,
    )
    ddl_text = normalize_snowflake_proposal_ddl(ddl_text, target_schema)
    connector = connector_service.create_connector_instance("snowflake", connector_config)
    try:
        from services.sql_connector_service import split_sql_statements

        statements = split_sql_statements(ddl_text)
        if not statements:
            raise HTTPException(status_code=400, detail="No executable SQL statements found in DDL")
        for stmt in statements:
            await connector.execute_non_query(stmt)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to execute DDL: {str(e)}")
    finally:
        try:
            await connector.close_connection()
        except Exception:
            pass

    return _json_response(
        {
            "status": "success",
            "message": "DDL executed successfully",
            "executed_statements": len(statements),
            "proposal_id": proposal_id,
        }
    )


@router.get("/{graph_id}/export")
async def export_enterprise_graph(
    graph_id: str,
    format: str = "json",
    user_id: UUID = Depends(get_current_user),
):
    graph = await _get_graph_or_404(graph_id, user_id)
    if format.lower() != "json":
        raise HTTPException(status_code=400, detail="Only format=json is supported in v1")

    proposal = await store.get_latest_warehouse_proposal(graph_id)
    lake_proposal = await store.get_latest_lake_proposal(graph_id)
    run = await store.get_latest_completed_run(graph_id, str(user_id))
    payload = {
        "graph": graph.to_dict(),
        "latest_run_id": run.get("id") if run else None,
        "report_snapshot": run.get("report_snapshot") if run else None,
        "warehouse_proposal": proposal,
        "lake_proposal": lake_proposal,
        "canonical_entities": await store.list_canonical_entities(graph_id),
        "semantic_mappings": await store.list_semantic_mappings(graph_id),
    }
    return _json_response({"status": "success", "export": payload})


@router.get("/{graph_id}/canonical-entities")
async def list_enterprise_graph_canonical_entities(
    graph_id: str,
    user_id: UUID = Depends(get_current_user),
):
    await _get_graph_or_404(graph_id, user_id)
    entities = await store.list_canonical_entities(graph_id)
    return _json_response({"status": "success", "canonical_entities": entities})


@router.get("/{graph_id}/members/{member_id}/connector-id")
async def get_member_connector_id(
    graph_id: str,
    member_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Helper for UI: resolve connector_id from stored connector_ref."""
    await _get_graph_or_404(graph_id, user_id)
    member = await store.get_member(graph_id, member_id)
    if not member:
        raise HTTPException(status_code=404, detail="Graph member not found")
    return _json_response(
        {
            "status": "success",
            "connector_type": member.connector_type,
            "connector_id": connector_id_from_ref(member.connector_type, member.connector_ref),
        }
    )
