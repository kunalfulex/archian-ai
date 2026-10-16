"""Reusable flow reuse endpoint and flow re-run starters."""

import asyncio
import json
import traceback
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db.connection import database
from db.extracted_schema import get_flow_details
from handlers.google_sheets_handler import process_google_sheets_extraction_background
from handlers.salesforce_handler import start_salesforce_extraction_job
from routes.auth import get_current_user
from services.flow_executions_service import FlowExecutionsService
from services.flow_migration_background import (
    _resolve_destination_context,
    process_google_sheets_flow_migration_background,
    process_salesforce_flow_migration_background,
    process_sql_flow_migration_background,
)
from services.incremental_sync_service import compute_query_fingerprint, IncrementalSyncService
from services.sql_background_processing_service import start_sql_extraction_task
from services.sql_connector_service import SQLConnectorService
from utils.job_stores import extraction_jobs
from utils.task_limiter import create_limited_task

router = APIRouter()


async def start_sql_flow_rerun(
    flow_id: str,
    user_prompt: str,
    user_id: UUID,
    flow_execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared helper to kick off a SQL flow re-run."""
    print(f"🔄 [SQL FLOW RERUN] Starting SQL flow rerun - flow_id: {flow_id}, user_id: {user_id}")
    print(f"🔄 [SQL FLOW RERUN] User prompt: {user_prompt[:100] if user_prompt else 'None'}...")
    
    flow_details = await get_flow_details(flow_id, user_id)
    print(f"🔄 [SQL FLOW RERUN] Flow details retrieved - status: {flow_details.get('status') if flow_details else 'None'}")

    if not flow_details or flow_details.get("status") != "success":
        print(f"❌ [SQL FLOW RERUN] Flow not found or invalid - flow_id: {flow_id}")
        raise HTTPException(status_code=404, detail="Flow not found")

    schema_data = flow_details.get("schema")
    if not schema_data:
        raise HTTPException(status_code=400, detail="Flow schema not found")

    if schema_data.get("extracted_schema_type") != "sql":
        raise HTTPException(status_code=400, detail="This is not a SQL flow")

    connector_id = schema_data.get("connector_id")

    from services.flow.connector_resolve import resolve_sql_flow_connector

    flow_info = flow_details.get("flow") or {}
    source_name = flow_info.get("source_name") if isinstance(flow_info, dict) else None
    connector_id = await resolve_sql_flow_connector(
        flow_id=flow_id,
        user_id=user_id,
        connector_id=str(connector_id) if connector_id else None,
        source_name=source_name,
    )

    from services.sql_connector_service import SQLConnectorService

    connector_service = SQLConnectorService()
    connector_data = await connector_service.get_connector_by_id(connector_id, user_id)

    if not connector_data:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "CONNECTION_STALE",
                "message": "SQL connection for this flow is no longer available.",
                "connector_type": "sql",
                "side": "source",
            },
        )

    sql_query = schema_data.get("sql_query")
    table_name = schema_data.get("table_name")

    if not sql_query:
        raise HTTPException(status_code=400, detail="SQL query not found in flow schema")

    if not isinstance(flow_info, dict):
        flow_info = {}
    sq_for_fp = sql_query if isinstance(sql_query, str) else str(sql_query)
    fp = compute_query_fingerprint(sq_for_fp)
    await IncrementalSyncService.clear_watermark_on_fingerprint_mismatch(
        flow_id,
        str(user_id),
        bool(flow_info.get("incremental_sync_enabled")),
        flow_info.get("incremental_query_fingerprint"),
        fp,
    )
    flow_row = await IncrementalSyncService.fetch_incremental_row(flow_id, str(user_id))
    # DB row may be a Record; normalize to dict so .get("connector_type") is reliable.
    _conn_raw = connector_data.get("connector")
    try:
        _conn_row = dict(_conn_raw) if _conn_raw is not None else {}
    except (TypeError, ValueError):
        _conn_row = {}
    dialect = _conn_row.get("connector_type") or "postgresql"
    _stor_fp = (flow_row or {}).get("incremental_query_fingerprint")
    _wm = (flow_row or {}).get("incremental_high_watermark")
    print(
        "[SQL INCREMENTAL DEBUG] rerun context "
        f"flow_id={flow_id} incremental_sync_enabled={bool(flow_row and flow_row.get('incremental_sync_enabled'))} "
        f"incremental_cursor_field={repr((flow_row or {}).get('incremental_cursor_field'))} "
        f"incremental_high_watermark_is_null={_wm is None} "
        f"fingerprint_stored[:16]={str(_stor_fp)[:16] if _stor_fp else None!r} "
        f"fingerprint_current[:16]={fp[:16]}"
    )
    sql_query = IncrementalSyncService.prepare_sql_rerun_query(
        sql_query if isinstance(sql_query, str) else str(sql_query),
        dialect,
        flow_row,
        sq_for_fp,
    )

    table_and_query = {
        "table_name": table_name,
        "query": sql_query,
        "comprehensive_prompt": user_prompt,
    }
    dest_id = flow_info.get("org_id")
    dest_type, dest_name = await _resolve_destination_context(
        user_id,
        flow_info.get("destination_type"),
        dest_id,
    )

    # Extract extraction_rules from migration_rules if available (same normalization as GSheets reuse)
    migration_rules = flow_info.get("migration_rules") or {}
    if isinstance(migration_rules, str):
        try:
            migration_rules = json.loads(migration_rules)
        except Exception:
            migration_rules = {}
    if not isinstance(migration_rules, dict):
        migration_rules = {}
    extraction_rules = migration_rules.get("extraction_rules")
    print(f"🔄 [SQL FLOW RERUN] Extraction rules from flow - has_extraction_rules: {bool(extraction_rules)}")

    print(f"🔄 [SQL FLOW RERUN] Starting SQL extraction task - destination: {dest_type}, destination_name: {dest_name}")
    task_id = await start_sql_extraction_task(
        connector_data,
        table_and_query,
        str(user_id),
        user_prompt,
        destination=dest_type,
        destination_name=dest_name,
        extraction_rules=extraction_rules,  # Pass saved extraction rules
        flow_execution_id=flow_execution_id,
    )
    print(f"✅ [SQL FLOW RERUN] SQL extraction task started - task_id: {task_id}")

    print(f"🔄 [SQL FLOW RERUN] Creating background migration task - task_id: {task_id}, flow_id: {flow_id}")
    await create_limited_task(
        process_sql_flow_migration_background(
            task_id=task_id,
            flow_id=flow_id,
            user_id=str(user_id),
            flow_execution_id=flow_execution_id,
        )
    )
    print(f"✅ [SQL FLOW RERUN] Background migration task created successfully")

    return {
        "status": "success",
        "job_id": task_id,
        "message": "SQL flow re-run started successfully",
        "action": "sql_rerun",
        "destination_type": dest_type,
        "destination_name": dest_name,
    }


async def _flow_destination_still_connected(
    user_id: UUID,
    flow_data: Dict[str, Any],
    schema_data: Optional[Dict[str, Any]],
) -> bool:
    """
    True if the saved destination still has a connection row for this user (Salesforce token,
    SQL connector, or Google Sheets destination spreadsheet). Used to avoid cluttering source
    errors with destination details when the destination is fine.
    """
    dtype = (flow_data.get("destination_type") or "salesforce").lower()
    org_or_dest_id = flow_data.get("org_id")
    schema_connector = (schema_data or {}).get("connector_id") if schema_data else None
    uid = str(user_id)

    if dtype == "salesforce" and org_or_dest_id:
        row = await database.fetch_one(
            """
            SELECT 1 AS ok
            FROM salesforce_tokens
            WHERE user_id = :uid AND org_id = :oid
            LIMIT 1
            """,
            {"uid": uid, "oid": str(org_or_dest_id)},
        )
        return row is not None

    if dtype == "sql" and org_or_dest_id:
        row = await database.fetch_one(
            """
            SELECT 1 AS ok
            FROM sql_connectors
            WHERE user_id = :uid AND id = :cid
            LIMIT 1
            """,
            {"uid": uid, "cid": str(org_or_dest_id)},
        )
        return row is not None

    if dtype == "google_sheets":
        lookup = org_or_dest_id or schema_connector
        if not lookup:
            return False
        ref = str(lookup).strip()
        try:
            UUID(ref)
            row = await database.fetch_one(
                """
                SELECT 1 AS ok
                FROM user_google_spreadsheets
                WHERE user_id = :uid AND id = :ugs_id
                LIMIT 1
                """,
                {"uid": uid, "ugs_id": ref},
            )
        except (ValueError, TypeError):
            row = None
        if row is None:
            row = await database.fetch_one(
                """
                SELECT 1 AS ok
                FROM user_google_spreadsheets
                WHERE user_id = :uid AND spreadsheet_id = :sid
                LIMIT 1
                """,
                {"uid": uid, "sid": ref},
            )
        return row is not None

    # Other / legacy types — omit destination from the message (same as “connected” for UX)
    return True


def _gsheets_reuse_unlinked_labels(
    flow_details: Optional[Dict[str, Any]],
    *,
    destination_connected: bool,
) -> Dict[str, Any]:
    """
    When user_google_spreadsheets has no row for the saved connector_id, build labels from
    stored flow/schema (spreadsheet title, tab, migration name, destination display name).
    """
    fd = (flow_details or {}).get("flow") or {}
    schema = (flow_details or {}).get("schema") or {}
    ext = schema.get("extracted_schema") or {}
    if isinstance(ext, str):
        try:
            ext = json.loads(ext)
        except Exception:
            ext = {}
    if not isinstance(ext, dict):
        ext = {}

    migration_rules = fd.get("migration_rules") or {}
    if isinstance(migration_rules, str):
        try:
            migration_rules = json.loads(migration_rules)
        except Exception:
            migration_rules = {}
    if not isinstance(migration_rules, dict):
        migration_rules = {}

    spreadsheet_title = (fd.get("source_name") or "").strip() or None
    if not spreadsheet_title:
        spreadsheet_title = (ext.get("spreadsheet_name") or ext.get("source_name") or "").strip() or None
    if not spreadsheet_title:
        er = migration_rules.get("extraction_rules") or {}
        if isinstance(er, dict):
            spreadsheet_title = (er.get("spreadsheet_name") or "").strip() or None

    tab_name = (schema.get("table_name") or ext.get("sheet_name") or "").strip() or None
    flow_name = (fd.get("migration_name") or "").strip() or None

    if spreadsheet_title and tab_name and tab_name.lower() != spreadsheet_title.lower():
        source_label = f"{spreadsheet_title} ({tab_name})"
    elif spreadsheet_title:
        source_label = spreadsheet_title
    elif tab_name:
        source_label = f"sheet tab “{tab_name}”"
    elif flow_name:
        source_label = f"flow “{flow_name}”"
    else:
        source_label = "saved Google Sheet source"

    dtype = (fd.get("destination_type") or "salesforce").lower()
    dname = (fd.get("destination_name") or "").strip()
    if dname:
        if dtype == "salesforce":
            dest_label = f"Salesforce — {dname}"
        elif dtype == "sql":
            dest_label = f"SQL — {dname}"
        elif dtype == "google_sheets":
            dest_label = f"Google Sheets — {dname}"
        else:
            dest_label = f"{dtype} — {dname}"
    else:
        dest_label = dtype.replace("_", " ").title() if dtype else "destination"

    if destination_connected:
        message = (
            f"{source_label} is no longer connected as the data source. "
            "Reconnect that Google Sheet or update this flow’s source, then try again."
        )
    else:
        message = (
            f"{source_label} is no longer connected as the data source. "
            f"Destination ({dest_label}) is not available. Reconnect the Google Sheet and destination, "
            "or update this flow, then try again."
        )

    return {
        "source_label": source_label,
        "destination_label": dest_label,
        "flow_name": flow_name or "",
        "message": message,
        "destination_connected": destination_connected,
    }


async def start_google_sheets_flow_rerun(
    flow_id: str,
    user_prompt: str,
    user_id: UUID,
    flow_execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared helper to kick off a Google Sheets flow re-run."""
    flow_details = await get_flow_details(flow_id, user_id)

    if not flow_details or flow_details.get("status") != "success":
        print(f"❌ [GSHEETS FLOW RERUN] Flow not found or invalid - flow_id: {flow_id}")
        raise HTTPException(
            status_code=404,
            detail={
                "code": "FLOW_NOT_FOUND",
                "message": "Flow not found",
            },
        )

    schema_data = flow_details.get("schema")
    if not schema_data:
        raise HTTPException(status_code=400, detail="Flow schema not found")

    if schema_data.get("extracted_schema_type") != "gsheets":
        raise HTTPException(status_code=400, detail="This is not a Google Sheets flow")

    connector_id = schema_data.get("connector_id")
    if not connector_id:
        raise HTTPException(status_code=400, detail="Google Sheets spreadsheet ID not found in flow")

    spreadsheet_record = await database.fetch_one(
        """
        SELECT ugs.spreadsheet_id, ugs.spreadsheet_name, ugs.sheet_name, 
               ugs.google_sheets_connection_id
        FROM user_google_spreadsheets ugs
        WHERE ugs.id = :connector_id AND ugs.user_id = :user_id
        """,
        {"connector_id": connector_id, "user_id": str(user_id)},
    )

    if not spreadsheet_record:
        dest_ok = await _flow_destination_still_connected(
            user_id,
            flow_details.get("flow") or {},
            schema_data,
        )
        labels = _gsheets_reuse_unlinked_labels(flow_details, destination_connected=dest_ok)
        raise HTTPException(
            status_code=404,
            detail={
                "code": "GOOGLE_SHEET_NOT_LINKED",
                "message": labels["message"],
                "source_label": labels["source_label"],
                "destination_label": labels["destination_label"],
                "destination_connected": labels.get("destination_connected"),
                "flow_name": labels.get("flow_name") or None,
            },
        )

    dest_type, dest_name = await _resolve_destination_context(
        user_id,
        flow_details.get("flow").get("destination_type"),
        flow_details.get("flow").get("org_id"),
    )

    # Extract extraction_rules from migration_rules if available
    flow_info = flow_details.get("flow") or {}
    migration_rules = flow_info.get("migration_rules") or {}
    extraction_rules = migration_rules.get("extraction_rules")

    job_id = str(uuid.uuid4())

    # Create job SYNCHRONOUSLY in Redis first (like Salesforce does) to avoid race condition
    # This ensures the job exists before migration task starts checking
    job_payload = {
        "status": "processing",
        "progress": 0,
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat(),
        "extraction_goals": user_prompt,
        "source_type": "google_sheets",
        "spreadsheet_name": spreadsheet_record["spreadsheet_name"],
        "sheet_name": spreadsheet_record["sheet_name"],
        "destination": dest_type,
        "destination_name": dest_name,
        "user_id": str(user_id),
        "job_type": "google_sheets_extraction",
    }
    if flow_execution_id:
        job_payload["flow_execution_id"] = flow_execution_id
    await extraction_jobs.set(job_id, job_payload)

    # Now start the background extraction task (job already exists)
    asyncio.create_task(
        process_google_sheets_extraction_background(
            job_id=job_id,
            spreadsheet_id=spreadsheet_record["spreadsheet_id"],
            spreadsheet_name=spreadsheet_record["spreadsheet_name"],
            sheet_name=spreadsheet_record["sheet_name"],
            range_name="A1:ZZ",
            extraction_goals=user_prompt,
            user_id=str(user_id),
            google_sheets_connection_id=spreadsheet_record["google_sheets_connection_id"],
            destination=dest_type,
            destination_name=dest_name,
            extraction_rules=extraction_rules,  # Pass saved extraction rules
        )
    )

    await create_limited_task(
        process_google_sheets_flow_migration_background(
            job_id=job_id,
            flow_id=flow_id,
            user_id=str(user_id),
            flow_execution_id=flow_execution_id,
        )
    )

    dest_display_name = dest_name or spreadsheet_record["spreadsheet_name"]

    return {
        "status": "success",
        "job_id": job_id,
        "message": "Google Sheets flow re-run started successfully. We will email you when the migration is complete.",
        "action": "gsheets_rerun",
        "destination_type": dest_type,
        "destination_name": dest_display_name,
    }


class ReuseFlowRequest(BaseModel):
    flow_id: str
    user_prompt: Optional[str] = None


@router.post("/flow/reuse")
async def reuse_flow(
    request: ReuseFlowRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Unified endpoint to reuse a saved flow based on its source/destination."""
    try:
        res = await reuse_flow_internal(
            request.flow_id,
            str(user_id),
            request.user_prompt or "",
            trigger_type="manual",
        )
        if res.get("status") == "error":
            msg = res.get("message", "Error")
            if msg == "Flow not found":
                raise HTTPException(
                    status_code=404,
                    detail={"code": "FLOW_NOT_FOUND", "message": msg},
                )
            raise HTTPException(status_code=500, detail=msg)
        return res
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error reusing flow {request.flow_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to reuse flow")


async def reuse_flow_internal(
    flow_id: str,
    user_id: str,
    user_prompt: str = "",
    trigger_type: str = "manual",
) -> Dict[str, Any]:
    """
    Internal function to reuse a flow without HTTP dependencies.
    Used by scheduler service to execute scheduled flows (trigger_type='scheduled').
    """
    execution_id: Optional[str] = None
    try:
        user_uuid = UUID(user_id)
        flow_details = await get_flow_details(flow_id, user_uuid)

        if not flow_details or flow_details.get("status") != "success":
            return {"status": "error", "message": "Flow not found"}

        execution_id = await FlowExecutionsService.create_execution(str(user_id), flow_id, trigger_type)

        flow_info = flow_details.get("flow") or {}
        schema_data = flow_details.get("schema") or {}

        source_type = (schema_data.get("extracted_schema_type") or "file").lower()
        destination_type = (flow_info.get("destination_type") or "salesforce").lower()
        prompt = user_prompt or schema_data.get("user_prompt") or ""

        if source_type == "sql":
            result = await start_sql_flow_rerun(
                flow_id=flow_id,
                user_prompt=prompt,
                user_id=user_uuid,
                flow_execution_id=execution_id,
            )
            if execution_id:
                result["flow_execution_id"] = execution_id
            await _maybe_run_bidirectional_reverse_leg(flow_info, str(user_id), prompt, trigger_type)
            return result

        if source_type == "gsheets":
            result = await start_google_sheets_flow_rerun(
                flow_id=flow_id,
                user_prompt=prompt,
                user_id=user_uuid,
                flow_execution_id=execution_id,
            )
            if execution_id:
                result["flow_execution_id"] = execution_id
            await _maybe_run_bidirectional_reverse_leg(flow_info, str(user_id), prompt, trigger_type)
            return result

        if source_type == "salesforce":
            result = await start_salesforce_flow_rerun(
                flow_id=flow_id,
                user_prompt=prompt,
                user_id=user_uuid,
                flow_execution_id=execution_id,
            )
            if execution_id:
                result["flow_execution_id"] = execution_id
            await _maybe_run_bidirectional_reverse_leg(flow_info, str(user_id), prompt, trigger_type)
            return result

        if execution_id:
            await FlowExecutionsService.complete_execution(execution_id, "pending_upload", None)

        return {
            "status": "pending_upload",
            "action": "file_upload",
            "flow_id": flow_id,
            "source_type": source_type,
            "destination_type": destination_type,
            "message": "This flow requires uploading a new file before it can run again.",
            "user_prompt": prompt,
            **({"flow_execution_id": execution_id} if execution_id else {}),
        }

    except HTTPException as he:
        if execution_id:
            try:
                await FlowExecutionsService.complete_execution(
                    execution_id, "failed", str(he.detail)[:2000] if he.detail else "HTTP error"
                )
            except Exception:
                pass
        raise
    except Exception as e:
        traceback.print_exc()
        print(f"Error in internal flow reuse {flow_id}: {type(e).__name__}: {e!r}")
        if execution_id:
            try:
                await FlowExecutionsService.complete_execution(execution_id, "failed", str(e)[:2000])
            except Exception:
                pass
        return {"status": "error", "message": f"Failed to reuse flow: {str(e)}"}


async def _maybe_run_bidirectional_reverse_leg(
    flow_info: dict,
    user_id: str,
    user_prompt: str,
    trigger_type: str,
) -> None:
    """After a successful parent-leg reuse setup, run the linked reverse flow if bidirectional is enabled."""
    if not flow_info or flow_info.get("parent_flow_id"):
        return
    if not flow_info.get("bidirectional_sync_enabled"):
        return
    rid = flow_info.get("reverse_flow_id")
    if not rid:
        return
    try:
        await reuse_flow_internal(str(rid), user_id, user_prompt or "", trigger_type)
    except Exception as e:
        print(f"⚠️ Bidirectional reverse leg failed for flow {rid}: {e}")


async def start_salesforce_flow_rerun(
    flow_id: str,
    user_prompt: str,
    user_id: UUID,
    flow_execution_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Kick off a Salesforce extraction flow based on saved flow metadata."""
    print(f"🔄 [SALESFORCE FLOW RERUN] Starting Salesforce flow rerun - flow_id: {flow_id}, user_id: {user_id}")
    print(f"🔄 [SALESFORCE FLOW RERUN] User prompt: {user_prompt[:100] if user_prompt else 'None'}...")
    
    flow_details = await get_flow_details(flow_id, user_id)
    print(f"🔄 [SALESFORCE FLOW RERUN] Flow details retrieved - status: {flow_details.get('status') if flow_details else 'None'}")

    if not flow_details or flow_details.get("status") != "success":
        print(f"❌ [SALESFORCE FLOW RERUN] Flow not found or invalid - flow_id: {flow_id}")
        raise HTTPException(status_code=404, detail="Flow not found")

    flow_info = flow_details.get("flow") or {}
    schema_data = flow_details.get("schema")

    if not schema_data:
        raise HTTPException(status_code=400, detail="Flow schema not found")

    if schema_data.get("extracted_schema_type") != "salesforce":
        raise HTTPException(status_code=400, detail="This is not a Salesforce flow")

    soql_query = schema_data.get("sql_query")
    object_api_name = schema_data.get("table_name")
    token_id = schema_data.get("connector_id") or schema_data.get("salesforce_token_id")

    if not soql_query or not object_api_name:
        raise HTTPException(status_code=400, detail="Saved flow is missing SOQL or object metadata")

    from services.flow.connector_resolve import resolve_salesforce_flow_source_token

    source_name = flow_info.get("source_name") or schema_data.get("source_name")
    token_row = await resolve_salesforce_flow_source_token(
        flow_id=flow_id,
        user_id=user_id,
        token_id=str(token_id) if token_id else None,
        source_name=source_name,
    )
    token_row_dict = dict(token_row)

    org_info = {
        "org_id": token_row_dict["org_id"],
        "connection_name": token_row_dict.get("org_display_name") or token_row_dict.get("instance_url"),
        "environment_type": token_row_dict.get("environment_type") or "production",
        "token_id": str(token_row_dict["id"]),
    }

    # Resolve object label if possible
    object_label = object_api_name
    object_row = await database.fetch_one(
        """
        SELECT label
        FROM salesforce_objects
        WHERE user_id = :user_id AND object_api_name = :object_api_name AND org_id = :org_id
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"user_id": str(user_id), "object_api_name": object_api_name, "org_id": token_row_dict["org_id"]},
    )
    object_row_dict = dict(object_row) if object_row else None
    if object_row_dict and object_row_dict.get("label"):
        object_label = object_row_dict["label"]

    sq_for_fp = soql_query if isinstance(soql_query, str) else str(soql_query)
    fp_sf = compute_query_fingerprint(sq_for_fp)
    await IncrementalSyncService.clear_watermark_on_fingerprint_mismatch(
        flow_id,
        str(user_id),
        bool(flow_info.get("incremental_sync_enabled")),
        flow_info.get("incremental_query_fingerprint"),
        fp_sf,
    )
    flow_row_inc = await IncrementalSyncService.fetch_incremental_row(flow_id, str(user_id))
    soql_query = IncrementalSyncService.prepare_salesforce_rerun_soql(
        soql_query if isinstance(soql_query, str) else str(soql_query),
        flow_row_inc,
        sq_for_fp,
    )

    soql_payload = {
        "soql": soql_query,
        "filter_summary": schema_data.get("filter_summary"),
        "used_fields": schema_data.get("used_fields"),
    }

    # Check if attachments should be extracted based on _attachments key in extracted_schema
    extracted_schema = schema_data.get("extracted_schema", {})
    include_attachments = "_attachments" in extracted_schema if isinstance(extracted_schema, dict) else False
    print(f"🔄 [SALESFORCE FLOW RERUN] Attachment extraction check - include_attachments: {include_attachments} (found _attachments key: {'_attachments' in extracted_schema if isinstance(extracted_schema, dict) else False})")

    object_result = {
        "object_api_name": object_api_name,
        "object_label": object_label,
        "needs_clarification": False,
        "include_attachments": include_attachments,  # Include attachment extraction flag for reusable flows
    }

    dest_type, dest_name = await _resolve_destination_context(
        user_id,
        flow_info.get("destination_type"),
        flow_info.get("org_id"),
    )

    # Extract extraction_rules from migration_rules if available
    migration_rules = flow_info.get("migration_rules") or {}
    extraction_rules = migration_rules.get("extraction_rules")
    print(f"🔄 [SALESFORCE FLOW RERUN] Extraction rules from flow - has_extraction_rules: {bool(extraction_rules)}")

    print(f"🔄 [SALESFORCE FLOW RERUN] Starting Salesforce extraction - destination: {dest_type}, destination_name: {dest_name}")
    job_id = await start_salesforce_extraction_job(
        user_id=str(user_id),
        org_info=org_info,
        object_result=object_result,
        soql_payload=soql_payload,
        destination=dest_type,
        destination_name=dest_name,
        extraction_prompt=user_prompt or schema_data.get("user_prompt") or "",
        extraction_rules=extraction_rules,  # Pass saved extraction rules
    )
    print(f"✅ [SALESFORCE FLOW RERUN] Salesforce extraction job started - job_id: {job_id}")

    if flow_execution_id:
        try:
            await extraction_jobs.update(job_id, {"flow_execution_id": flow_execution_id})
        except Exception as e:
            print(f"⚠️ Could not attach flow_execution_id to extraction job: {e}")

    print(f"🔄 [SALESFORCE FLOW RERUN] Creating background migration task - job_id: {job_id}, flow_id: {flow_id}")
    await create_limited_task(
        process_salesforce_flow_migration_background(
            job_id=job_id,
            flow_id=flow_id,
            user_id=str(user_id),
            flow_execution_id=flow_execution_id,
        )
    )
    print(f"✅ [SALESFORCE FLOW RERUN] Background migration task created successfully")

    return {
        "status": "success",
        "job_id": job_id,
        "message": "Salesforce flow re-run started successfully",
        "action": "salesforce_rerun",
        "destination_type": dest_type,
        "destination_name": dest_name,
    }
