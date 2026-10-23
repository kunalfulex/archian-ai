"""Migration and date-range export endpoints and background helpers."""

import asyncio
import csv
import json
import os
import secrets
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from db.connection import database, export_database
from routes.auth import get_current_user
from services.migration_records_service import MigrationRecordsService
from utils.email_utils import send_export_ready_email, send_migration_export_ready_email
from utils.url_defaults import DEFAULT_BACKEND_URL
from utils.user_email import get_user_email

router = APIRouter()


class ExportMigrationDataRequest(BaseModel):
    start_date: str
    end_date: str


@router.post("/migration-records/batch/{batch_id}/export")
async def start_migration_export(
    batch_id: str,
    status: str = Query("all", description="success | failed | all"),
    user_id: UUID = Depends(get_current_user),
):
    """Start background export of migration records. Returns 202 + task_id. Poll /migration-export-status/{task_id} for progress."""
    if status not in ("success", "failed", "all"):
        raise HTTPException(status_code=400, detail="status must be success, failed, or all")
    batch_row = await database.fetch_one(
        """
        SELECT id, user_id, batch_name, document_type, destination_type, mapping_config, detail_purged_at
        FROM migration_batches WHERE id = :batch_id
        """,
        values={"batch_id": batch_id},
    )
    if not batch_row:
        raise HTTPException(status_code=404, detail="Migration batch not found")
    batch_dict = dict(batch_row)
    if str(batch_dict["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied to this migration batch")
    if batch_dict.get("detail_purged_at") is not None:
        raise HTTPException(
            status_code=410,
            detail={
                "message": "Migration record detail is no longer available for this batch (retention policy).",
                "code": "MIGRATION_DETAIL_EXPIRED",
            },
        )
    # Extract source/destination metadata for export columns
    mapping_config = batch_dict.get("mapping_config")
    if isinstance(mapping_config, str):
        try:
            mapping_config = json.loads(mapping_config) if mapping_config else {}
        except Exception:
            mapping_config = {}
    elif mapping_config is None:
        mapping_config = {}
    dest_type = (batch_dict.get("destination_type") or "salesforce").lower()
    source_type = mapping_config.get("source_type") or ""
    source_name = (
        mapping_config.get("source_name")
        or mapping_config.get("document_type")
        or mapping_config.get("table_name")
        or mapping_config.get("spreadsheet_name")
        or ""
    )
    destination_name = (
        mapping_config.get("destination_name")
        or batch_dict.get("document_type")
        or MigrationRecordsService._extract_destination_name(dest_type, mapping_config)
        or dest_type.replace("_", " ").title()
    )
    count = await MigrationRecordsService.count_migration_records_batch(batch_id, status if status != "all" else None)
    use_csv = count > MIGRATION_EXPORT_MAX_RECORDS
    await export_database.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_export_tasks (
            id VARCHAR PRIMARY KEY,
            user_id UUID NOT NULL,
            batch_id VARCHAR NOT NULL,
            status_filter VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            file_path TEXT,
            download_token VARCHAR(64),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
        """
    )
    task_id = str(uuid.uuid4())
    await export_database.execute(
        """INSERT INTO migration_export_tasks (id, user_id, batch_id, status_filter, status)
           VALUES (:id, :user_id, :batch_id, :status_filter, 'pending')""",
        values={
            "id": task_id,
            "user_id": str(user_id),
            "batch_id": batch_id,
            "status_filter": status,
        },
    )
    task = asyncio.create_task(
        process_migration_export_task(
            task_id=task_id,
            batch_id=batch_id,
            user_id=str(user_id),
            status_filter=status,
            batch_name=batch_dict.get("batch_name") or "",
            document_type=batch_dict.get("document_type") or "",
            source_type=source_type,
            source_name=source_name,
            destination_type=dest_type,
            destination_name=destination_name,
            use_csv=use_csv,
        )
    )
    _migration_export_tasks.add(task)
    task.add_done_callback(_migration_export_tasks.discard)
    return JSONResponse(
        status_code=202,
        content={
            "message": "Export started." + (" Large dataset: CSV will be emailed when ready." if use_csv else ""),
            "task_id": task_id,
            "status": "pending",
            "large_export": use_csv,
        },
    )


@router.post("/migration-records/export")
async def start_migration_export_all(
    status: str = Query("all", description="success | failed | all"),
    user_id: UUID = Depends(get_current_user),
):
    """Start background export of all migration records for the current user (success/failed/all).
    Returns 202 + task_id. If large_export: CSV, email-only. Else: Excel, polling + email."""
    if status not in ("success", "failed", "all"):
        raise HTTPException(status_code=400, detail="status must be success, failed, or all")
    status_arg = status if status != "all" else None
    count = await MigrationRecordsService.count_migration_records_user_scoped(str(user_id), status_arg)
    use_csv = count > MIGRATION_EXPORT_MAX_RECORDS
    await export_database.execute(
        """
        CREATE TABLE IF NOT EXISTS migration_export_tasks (
            id VARCHAR PRIMARY KEY,
            user_id UUID NOT NULL,
            batch_id VARCHAR NOT NULL,
            status_filter VARCHAR(20) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            file_path TEXT,
            download_token VARCHAR(64),
            error_message TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ
        )
        """
    )
    task_id = str(uuid.uuid4())
    await export_database.execute(
        """INSERT INTO migration_export_tasks (id, user_id, batch_id, status_filter, status)
           VALUES (:id, :user_id, :batch_id, :status_filter, 'pending')""",
        values={
            "id": task_id,
            "user_id": str(user_id),
            "batch_id": "__all__",
            "status_filter": status,
        },
    )
    task = asyncio.create_task(
        process_migration_export_user_task(
            task_id=task_id,
            user_id=str(user_id),
            status_filter=status,
            use_csv=use_csv,
        )
    )
    _migration_export_tasks.add(task)
    task.add_done_callback(_migration_export_tasks.discard)
    return JSONResponse(
        status_code=202,
        content={
            "message": "Export started." + (" Large dataset: CSV will be emailed when ready." if use_csv else ""),
            "task_id": task_id,
            "status": "pending",
            "large_export": use_csv,
        },
    )


@router.get("/migration-export-status/{task_id}")
async def get_migration_export_status(
    task_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Poll for migration export task status. When completed, returns download_token for /migration-export-download."""
    row = await export_database.fetch_one(
        "SELECT id, user_id, status, download_token, error_message FROM migration_export_tasks WHERE id = :id",
        values={"id": task_id},
    )
    if not row:
        raise HTTPException(status_code=404, detail="Export task not found")
    if str(row["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")
    status_val = row["status"]
    resp = {"task_id": task_id, "status": status_val}
    if status_val == "completed":
        resp["download_token"] = row["download_token"]
    elif status_val == "failed":
        resp["error_message"] = row["error_message"] or "Export failed"
    return resp


@router.get("/migration-export-download")
async def migration_export_download(token: str = Query(..., description="One-time download token")):
    """Download migration export file by token. Invalidates after first download. Supports .xlsx and .csv."""
    row = await export_database.fetch_one(
        """SELECT id, file_path FROM migration_export_tasks
           WHERE download_token = :token AND status = 'completed' AND file_path IS NOT NULL""",
        values={"token": token},
    )
    file_path = row["file_path"] if row else None
    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Download link invalid or expired")
    ext = ".csv" if file_path.lower().endswith(".csv") else ".xlsx"
    filename = f"migration-results-{row['id'][:8]}{ext}"
    media_type = "text/csv" if ext == ".csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    try:
        body = await asyncio.to_thread(_read_file_bytes, file_path)
        return Response(
            content=body,
            media_type=media_type,
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        def _remove_if_exists(p: str) -> None:
            try:
                os.remove(p)
            except OSError:
                pass

        await asyncio.to_thread(_remove_if_exists, file_path)
        await export_database.execute(
            "UPDATE migration_export_tasks SET file_path = NULL, download_token = NULL WHERE id = :id",
            values={"id": row["id"]},
        )


# Directory for background export files (create on first use)
EXPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "exports")


# In-flight export tasks so we can cancel them on shutdown (prevents server hang on Ctrl+C)
_export_tasks: set = set()


_migration_export_tasks: set = set()


# Max records for Excel export (configurable via env); above this we use CSV + email-only
MIGRATION_EXPORT_MAX_RECORDS = int(os.getenv("MIGRATION_EXPORT_MAX_RECORDS", "50000"))


async def _fetch_migration_records_for_export(user_id: str, start_date: str, end_date: str):
    """Fetch migration records for date range for export."""
    query = """
    SELECT 
        mr.id, mr.batch_id, mr.source_data, mr.mapped_data, mr.salesforce_results,
        mr.status, mr.created_at, mr.processed_at, mr.error_message,
        mb.batch_name, mb.document_type
    FROM migration_records mr
    JOIN migration_batches mb ON mr.batch_id = mb.id
    WHERE mb.user_id = :user_id 
    AND mr.created_at >= :start_date 
    AND mr.created_at <= :end_date
    ORDER BY mr.created_at DESC
    """
    start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
    end_datetime = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    return await export_database.fetch_all(
        query,
        values={"user_id": user_id, "start_date": start_datetime, "end_date": end_datetime},
    )


def _build_export_excel_bytes(records: list, start_date: str, end_date: str) -> bytes:
    """Build Excel file bytes from migration records."""
    export_data = []
    for record in records:
        record = dict(record)  # ensure dict so .get() works (asyncpg Record has no .get)
        source_data = {}
        mapped_data = {}
        salesforce_results = {}
        if record.get("source_data"):
            try:
                source_data = json.loads(record["source_data"]) if isinstance(record["source_data"], str) else record["source_data"]
            except Exception:
                source_data = {}
        if record.get("mapped_data"):
            try:
                mapped_data = json.loads(record["mapped_data"]) if isinstance(record["mapped_data"], str) else record["mapped_data"]
            except Exception:
                mapped_data = {}
        if record.get("salesforce_results"):
            try:
                salesforce_results = json.loads(record["salesforce_results"]) if isinstance(record["salesforce_results"], str) else record["salesforce_results"]
            except Exception:
                salesforce_results = {}
        flattened = {
            "Document Type": record.get("document_type") or "",
            "Status": record.get("status") or "",
            "Created At": record["created_at"].strftime("%Y-%m-%d %H:%M:%S") if record.get("created_at") else "",
            "Processed At": record["processed_at"].strftime("%Y-%m-%d %H:%M:%S") if record.get("processed_at") else "",
            "Error Message": record.get("error_message") or "",
        }
        if isinstance(source_data, dict):
            for k, v in source_data.items():
                flattened[f"Source_{str(k).replace('_', ' ').title()}"] = str(v) if v is not None else ""
        if isinstance(mapped_data, dict):
            for k, v in mapped_data.items():
                flattened[f"Mapped_{str(k).replace('_', ' ').title()}"] = str(v) if v is not None else ""
        if isinstance(salesforce_results, dict):
            for k, v in salesforce_results.items():
                flattened[f"Salesforce_{str(k).replace('_', ' ').title()}"] = str(v) if v is not None else ""
        export_data.append(flattened)
    df = pd.DataFrame(export_data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Migration Data", index=False)
        worksheet = writer.sheets["Migration Data"]
        for column in worksheet.columns:
            max_length = max((len(str(cell.value or "")) for cell in column), default=0)
            column_letter = column[0].column_letter
            worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)
    output.seek(0)
    return output.read()


def _write_bytes_to_file(path: str, data: bytes) -> None:
    """Write bytes to file. Use with asyncio.to_thread to avoid blocking the event loop."""
    with open(path, "wb") as f:
        f.write(data)


def _read_file_bytes(path: str) -> bytes:
    """Read file bytes. Use with asyncio.to_thread to avoid blocking the event loop."""
    with open(path, "rb") as f:
        return f.read()


def _append_csv_chunk(path: str, rows: List[Dict[str, Any]], write_header: bool) -> None:
    """Append CSV rows to file. Use with asyncio.to_thread. write_header=True for first chunk."""
    mode = "w" if write_header else "a"
    with open(path, mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_EXPORT_COLUMNS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        if rows:
            writer.writerows(rows)


def _build_and_write_export_file(records: list, start_date: str, end_date: str, file_path: str) -> None:
    """Build Excel from records and write to file. Runs in thread pool to avoid blocking the event loop."""
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    excel_bytes = _build_export_excel_bytes(records, start_date, end_date)
    with open(file_path, "wb") as f:
        f.write(excel_bytes)


def _extract_result_message(record: dict, salesforce_results: dict) -> str:
    """Extract human-readable result message from record (error_message or result payload)."""
    error_msg = record.get("error_message") or ""
    if error_msg:
        return error_msg
    if isinstance(salesforce_results, dict):
        for key, val in salesforce_results.items():
            if key == "error":
                continue
            if isinstance(val, dict):
                msg = val.get("message") or val.get("status")
                if msg:
                    return str(msg)
            elif val is not None:
                return str(val)
    return ""


def _flatten_record_for_csv(
    record: dict,
    source_type: str = "",
    source_name: str = "",
    destination_type: str = "",
    destination_name: str = "",
    document_type: str = "",
) -> dict:
    """Flatten a migration record to a CSV row (fixed columns for streaming)."""
    record = dict(record)
    source_data = {}
    mapped_data = {}
    salesforce_results = {}
    if record.get("source_data"):
        try:
            v = record["source_data"]
            source_data = json.loads(v) if isinstance(v, str) else v
        except Exception:
            pass
    if record.get("mapped_data"):
        try:
            v = record["mapped_data"]
            mapped_data = json.loads(v) if isinstance(v, str) else v
        except Exception:
            pass
    if record.get("salesforce_results"):
        try:
            v = record["salesforce_results"]
            salesforce_results = json.loads(v) if isinstance(v, str) else v
        except Exception:
            pass

    def _fmt_dt(v):
        if v is None:
            return ""
        if hasattr(v, "strftime"):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        return str(v)

    result_status = record.get("status") or ""
    result_message = _extract_result_message(record, salesforce_results)
    src_type = record.get("_export_source_type", source_type)
    src_name = record.get("_export_source_name", source_name)
    dst_type = record.get("_export_destination_type", destination_type)
    dst_name = record.get("_export_destination_name", destination_name)

    return {
        "Source Type": src_type or "",
        "Source Name": src_name or "",
        "Destination Type": (dst_type or "").replace("_", " ").title(),
        "Destination Name": dst_name or "",
        "Result Status": result_status,
        "Result Message": result_message,
        "Document Type": document_type or "",
        "Created At": _fmt_dt(record.get("created_at")),
        "Processed At": _fmt_dt(record.get("processed_at")),
        "Error Message": record.get("error_message") or "",
        "Source Data": json.dumps(source_data, default=str) if source_data else "",
        "Mapped Data": json.dumps(mapped_data, default=str) if mapped_data else "",
        "Result Data": json.dumps(salesforce_results, default=str) if salesforce_results else "",
    }


CSV_EXPORT_COLUMNS = [
    "Source Type", "Source Name", "Destination Type", "Destination Name",
    "Result Status", "Result Message", "Document Type", "Created At", "Processed At",
    "Error Message", "Source Data", "Mapped Data", "Result Data",
]


def _build_migration_batch_export_excel_bytes(
    records: list,
    batch_name: str = "",
    document_type: str = "",
    source_type: str = "",
    source_name: str = "",
    destination_type: str = "",
    destination_name: str = "",
) -> bytes:
    """Build Excel bytes from migration batch records (for export). Includes Source/Destination/Result columns."""
    export_data = []
    for record in records:
        record = dict(record)
        source_data = {}
        mapped_data = {}
        salesforce_results = {}
        if record.get("source_data"):
            try:
                source_data = json.loads(record["source_data"]) if isinstance(record["source_data"], str) else record["source_data"]
            except Exception:
                source_data = {}
        if record.get("mapped_data"):
            try:
                mapped_data = json.loads(record["mapped_data"]) if isinstance(record["mapped_data"], str) else record["mapped_data"]
            except Exception:
                mapped_data = {}
        if record.get("salesforce_results"):
            try:
                salesforce_results = json.loads(record["salesforce_results"]) if isinstance(record["salesforce_results"], str) else record["salesforce_results"]
            except Exception:
                salesforce_results = {}

        created_at = record.get("created_at")
        processed_at = record.get("processed_at")

        def _fmt_dt(v):
            if v is None:
                return ""
            if hasattr(v, "strftime"):
                return v.strftime("%Y-%m-%d %H:%M:%S")
            return str(v)

        result_status = record.get("status") or ""
        result_message = _extract_result_message(record, salesforce_results)

        # Use per-record metadata when present (user-scoped export); else batch-level
        src_type = record.get("_export_source_type", source_type)
        src_name = record.get("_export_source_name", source_name)
        dst_type = record.get("_export_destination_type", destination_type)
        dst_name = record.get("_export_destination_name", destination_name)

        flattened = {
            "Source Type": src_type or "",
            "Source Name": src_name or "",
            "Destination Type": (dst_type or "").replace("_", " ").title(),
            "Destination Name": dst_name or "",
            "Result Status": result_status,
            "Result Message": result_message,
            "Document Type": document_type or "",
            "Status": result_status,  # Keep for backwards compatibility
            "Created At": _fmt_dt(created_at),
            "Processed At": _fmt_dt(processed_at),
            "Error Message": record.get("error_message") or "",
        }
        if isinstance(source_data, dict):
            for k, v in source_data.items():
                flattened[f"Source_{str(k).replace('_', ' ').title()}"] = str(v) if v is not None else ""
        if isinstance(mapped_data, dict):
            for k, v in mapped_data.items():
                flattened[f"Mapped_{str(k).replace('_', ' ').title()}"] = str(v) if v is not None else ""
        if isinstance(salesforce_results, dict):
            for k, v in salesforce_results.items():
                if v is None:
                    flattened[f"Result_{str(k).replace('_', ' ').title()}"] = ""
                elif isinstance(v, (dict, list)):
                    flattened[f"Result_{str(k).replace('_', ' ').title()}"] = json.dumps(v, default=str)
                else:
                    flattened[f"Result_{str(k).replace('_', ' ').title()}"] = str(v)
        export_data.append(flattened)
    df = pd.DataFrame(export_data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Migration Results", index=False)
        worksheet = writer.sheets["Migration Results"]
        for column in worksheet.columns:
            max_length = max((len(str(cell.value or "")) for cell in column), default=0)
            column_letter = column[0].column_letter
            worksheet.column_dimensions[column_letter].width = min(max_length + 2, 50)
    output.seek(0)
    return output.read()


async def process_migration_export_task(
    task_id: str,
    batch_id: str,
    user_id: str,
    status_filter: str,
    batch_name: str,
    document_type: str,
    source_type: str = "",
    source_name: str = "",
    destination_type: str = "",
    destination_name: str = "",
    use_csv: bool = False,
) -> None:
    """Background task: build Excel or stream CSV from migration records and save. Non-blocking."""
    try:
        status_arg = status_filter if status_filter != "all" else None
        if use_csv:
            file_path = os.path.join(EXPORTS_DIR, f"migration_export_{task_id}.csv")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            row_count = 0
            chunk: List[Dict[str, Any]] = []
            first_chunk = True
            async for batch in MigrationRecordsService.iterate_migration_records(
                batch_id, batch_size=500, status_filter=status_arg
            ):
                for r in batch:
                    flat = _flatten_record_for_csv(
                        r, source_type, source_name, destination_type, destination_name, document_type
                    )
                    chunk.append(flat)
                    row_count += 1
                    if len(chunk) >= 5000:
                        await asyncio.to_thread(_append_csv_chunk, file_path, chunk, write_header=first_chunk)
                        first_chunk = False
                        chunk = []
            if chunk:
                await asyncio.to_thread(_append_csv_chunk, file_path, chunk, write_header=first_chunk)
            if row_count == 0:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                await export_database.execute(
                    """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
                    values={"id": task_id, "msg": "No records found for the selected filter"},
                )
                return
        else:
            records: List[Dict[str, Any]] = []
            async for batch in MigrationRecordsService.iterate_migration_records(
                batch_id, batch_size=500, status_filter=status_arg
            ):
                for r in batch:
                    records.append(r)
                    if len(records) >= MIGRATION_EXPORT_MAX_RECORDS:
                        break
                if len(records) >= MIGRATION_EXPORT_MAX_RECORDS:
                    break
            if not records:
                await export_database.execute(
                    """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
                    values={"id": task_id, "msg": "No records found for the selected filter"},
                )
                return
            file_path = os.path.join(EXPORTS_DIR, f"migration_export_{task_id}.xlsx")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            excel_bytes = await asyncio.to_thread(
                _build_migration_batch_export_excel_bytes,
                records,
                batch_name,
                document_type,
                source_type,
                source_name,
                destination_type,
                destination_name,
            )
            await asyncio.to_thread(_write_bytes_to_file, file_path, excel_bytes)
        download_token = secrets.token_urlsafe(32)
        await export_database.execute(
            """UPDATE migration_export_tasks SET status = 'completed', download_token = :token, file_path = :path, completed_at = NOW() WHERE id = :id""",
            values={"id": task_id, "token": download_token, "path": file_path},
        )
        backend_url = os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")
        user_email = await get_user_email(user_id)
        if user_email:
            download_url = f"{backend_url}/migration-export-download?token={download_token}"
            export_desc = f"batch results ({status_filter})" + (" - CSV format" if use_csv else "")
            await send_migration_export_ready_email(user_email, download_url, export_desc)
    except Exception as e:
        print(f"❌ Migration export task {task_id} failed: {e}")
        await export_database.execute(
            """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
            values={"id": task_id, "msg": str(e)[:500]},
        )


async def process_migration_export_user_task(
    task_id: str,
    user_id: str,
    status_filter: str,
    use_csv: bool = False,
) -> None:
    """Background task: build Excel or stream CSV from all user migration records and save."""
    try:
        status_arg = status_filter if status_filter != "all" else None
        if use_csv:
            file_path = os.path.join(EXPORTS_DIR, f"migration_export_{task_id}.csv")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            row_count = 0
            chunk: List[Dict[str, Any]] = []
            first_chunk = True
            async for batch in MigrationRecordsService.iterate_migration_records_user_scoped(
                user_id, batch_size=500, status_filter=status_arg
            ):
                for r in batch:
                    flat = _flatten_record_for_csv(r, "", "", "", "", "")
                    chunk.append(flat)
                    row_count += 1
                    if len(chunk) >= 5000:
                        await asyncio.to_thread(_append_csv_chunk, file_path, chunk, write_header=first_chunk)
                        first_chunk = False
                        chunk = []
            if chunk:
                await asyncio.to_thread(_append_csv_chunk, file_path, chunk, write_header=first_chunk)
            if row_count == 0:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                await export_database.execute(
                    """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
                    values={"id": task_id, "msg": "No records found for the selected filter"},
                )
                return
        else:
            records: List[Dict[str, Any]] = []
            async for batch in MigrationRecordsService.iterate_migration_records_user_scoped(
                user_id, batch_size=500, status_filter=status_arg
            ):
                for r in batch:
                    records.append(r)
                    if len(records) >= MIGRATION_EXPORT_MAX_RECORDS:
                        break
                if len(records) >= MIGRATION_EXPORT_MAX_RECORDS:
                    break
            if not records:
                await export_database.execute(
                    """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
                    values={"id": task_id, "msg": "No records found for the selected filter"},
                )
                return
            file_path = os.path.join(EXPORTS_DIR, f"migration_export_{task_id}.xlsx")
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            excel_bytes = await asyncio.to_thread(
                _build_migration_batch_export_excel_bytes,
                records,
                batch_name="",
                document_type="",
                source_type="",
                source_name="",
                destination_type="",
                destination_name="",
            )
            await asyncio.to_thread(_write_bytes_to_file, file_path, excel_bytes)
        download_token = secrets.token_urlsafe(32)
        await export_database.execute(
            """UPDATE migration_export_tasks SET status = 'completed', download_token = :token, file_path = :path, completed_at = NOW() WHERE id = :id""",
            values={"id": task_id, "token": download_token, "path": file_path},
        )
        backend_url = os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")
        user_email = await get_user_email(user_id)
        if user_email:
            download_url = f"{backend_url}/migration-export-download?token={download_token}"
            export_desc = {"success": "successful migrations", "failed": "failed migrations", "all": "all migration records"}.get(status_filter, status_filter)
            if use_csv:
                export_desc += " - CSV format"
            await send_migration_export_ready_email(user_email, download_url, export_desc)
    except Exception as e:
        print(f"❌ Migration export user task {task_id} failed: {e}")
        await export_database.execute(
            """UPDATE migration_export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
            values={"id": task_id, "msg": str(e)[:500]},
        )


async def process_export_task(task_id: str, user_id: str, user_email: Optional[str], start_date: str, end_date: str):
    """Background task: build Excel, save to file, update task, send email with download link."""
    backend_url = os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")
    try:
        records = await _fetch_migration_records_for_export(user_id, start_date, end_date)
        if not records:
            await export_database.execute(
                """UPDATE export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
                values={"id": task_id, "msg": "No migration data found for the specified date range"},
            )
            return
        records_list = [dict(r) for r in records]
        file_path = os.path.join(EXPORTS_DIR, f"{task_id}.xlsx")
        # Run all CPU + disk I/O in thread pool so the event loop never blocks
        await asyncio.to_thread(_build_and_write_export_file, records_list, start_date, end_date, file_path)
        download_token = secrets.token_urlsafe(32)
        await export_database.execute(
            """UPDATE export_tasks SET status = 'completed', download_token = :token, file_path = :path, completed_at = NOW() WHERE id = :id""",
            values={"id": task_id, "token": download_token, "path": file_path},
        )
        if user_email:
            download_url = f"{backend_url}/export-download?token={download_token}"
            await send_export_ready_email(user_email, download_url, start_date, end_date)
    except Exception as e:
        print(f"❌ Export task {task_id} failed: {e}")
        await export_database.execute(
            """UPDATE export_tasks SET status = 'failed', completed_at = NOW(), error_message = :msg WHERE id = :id""",
            values={"id": task_id, "msg": str(e)[:500]},
        )


@router.post("/export-migration-data")
async def start_export_migration_data(
    body: ExportMigrationDataRequest,
    user_id: UUID = Depends(get_current_user),
):
    """Start a background export; user receives an email with download link when ready."""
    try:
        start_date = body.start_date
        end_date = body.end_date
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        if start_dt > end_dt:
            raise HTTPException(status_code=400, detail="Start date cannot be after end date")
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD")
    task_id = str(uuid.uuid4())
    await export_database.execute(
        """INSERT INTO export_tasks (id, user_id, status, start_date, end_date) VALUES (:id, :user_id, 'pending', :start_date, :end_date)""",
        values={"id": task_id, "user_id": str(user_id), "start_date": start_dt.date(), "end_date": end_dt.date()},
    )
    user_email = await get_user_email(str(user_id))
    task = asyncio.create_task(process_export_task(task_id, str(user_id), user_email, start_date, end_date))
    _export_tasks.add(task)
    task.add_done_callback(_export_tasks.discard)
    return JSONResponse(
        status_code=202,
        content={"message": "Export started. We'll email you a download link when it's ready.", "task_id": task_id},
    )


@router.get("/export-download")
async def export_download(token: str = Query(..., description="One-time download token from email")):
    """Stream export file by token; invalidates after first download."""
    row = await export_database.fetch_one(
        "SELECT id, file_path FROM export_tasks WHERE download_token = :token AND status = 'completed' AND file_path IS NOT NULL",
        values={"token": token},
    )
    # Record from databases has no .get(); use bracket access
    file_path = row["file_path"] if row else None
    if not file_path or not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="Download link invalid or expired")
    filename = f"migration-data-export.xlsx"
    try:
        with open(file_path, "rb") as f:
            body = f.read()
        return Response(
            content=body,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass
        await export_database.execute(
            "UPDATE export_tasks SET file_path = NULL, download_token = NULL WHERE id = :id",
            values={"id": row["id"]},
        )


async def cancel_in_flight_export_tasks() -> None:
    """Cancel background export tasks during app shutdown."""
    all_export_tasks = list(_export_tasks) + list(_migration_export_tasks)
    if not all_export_tasks:
        return
    for t in all_export_tasks:
        t.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*all_export_tasks, return_exceptions=True), timeout=2.0)
    except asyncio.TimeoutError:
        pass
