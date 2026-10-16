"""Persist Google Sheets chat operations (join, search) as extraction jobs in DB."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.google_sheets.models import SpreadsheetContext
from services.google_sheets.response_summary import json_safe_records
from utils.extraction_sse_payload import (
    build_job_status_after_db_save,
    extraction_job_finished_sse,
)


async def persist_gsheets_operation_as_extraction(
    *,
    user_id: str,
    conversation_id: Optional[str],
    classification: Optional[Dict[str, Any]],
    ctx: SpreadsheetContext,
    rows: List[Dict[str, Any]],
    extraction_goals: str,
    document_type: str,
    document_description: str,
    extraction_metadata: Optional[Dict[str, Any]] = None,
    operation_summary: Optional[str] = None,
) -> Tuple[str, str]:
    """
    Save rows to extraction_results and return (job_id, extraction_job_finished SSE line).
    """
    from services.extraction_results_service import ExtractionResultsService

    classification = dict(classification or {})
    job_id = str(uuid.uuid4())
    safe_rows = json_safe_records(rows)
    record_count = len(safe_rows)

    meta: Dict[str, Any] = dict(extraction_metadata or {})
    if ctx.sheet_name:
        meta.setdefault("sheet_name", ctx.sheet_name)
    meta.setdefault("spreadsheet_id", ctx.spreadsheet_id)
    meta.setdefault("operation", meta.get("operation") or "gsheets_chat")

    data_quality_report: Optional[Dict[str, Any]] = None
    if safe_rows:
        try:
            from utils.data_quality_analyzer import analyze_data_quality

            data_quality_report = await asyncio.to_thread(analyze_data_quality, safe_rows)
            score = (data_quality_report.get("summary") or {}).get("data_quality_score", 0)
            print(f"📊 GSheets operation data quality: Score {score}/100 ({record_count} rows)")
        except Exception as exc:
            print(f"⚠️ GSheets operation data quality analysis failed: {exc}")

    await ExtractionResultsService.save_extraction_result(
        job_id=job_id,
        user_id=user_id,
        extracted_data=safe_rows,
        source_type="google_sheets",
        extraction_goals=extraction_goals,
        document_type=document_type,
        document_description=document_description,
        source_name=ctx.spreadsheet_name,
        source_identifier=ctx.spreadsheet_id,
        parent_job_id=classification.get("parent_job_id"),
        conversation_id=conversation_id,
        has_cleanup_rules=False,
        has_transformation_rules=False,
        status="success",
        extraction_metadata=meta,
    )

    intent_state = None
    extraction_message_id = None
    if conversation_id:
        try:
            from handlers.classification_handler import create_intent_state
            from services.chat_service import ChatService

            destination = classification.get("destination") or "unknown"
            destination_name = classification.get("destination_name")
            active_intent = (
                "migrate" if destination and destination != "unknown" else "extract"
            )
            intent_state = create_intent_state(
                active_intent=active_intent,
                active_source="google_sheets",
                active_destination=destination,
                source_name=ctx.spreadsheet_name,
                destination_name=destination_name,
                job_id=job_id,
                state="extraction_completed",
            )
            msg = await ChatService.log_data_event(
                conversation_id=conversation_id,
                user_id=user_id,
                event_type="extraction_completed",
                event_data={
                    "job_id": job_id,
                    "source_type": "google_sheets",
                    "source_name": ctx.spreadsheet_name,
                    "record_count": record_count,
                    "status": "success",
                    "destination": destination,
                    "destination_name": destination_name,
                    "document_type": document_type,
                    "document_description": document_description,
                    "intent_state": intent_state,
                    "data_quality_report": data_quality_report,
                    "operation_summary": (operation_summary or "").strip() or None,
                    **{k: v for k, v in meta.items() if k.startswith("join_")},
                },
            )
            if msg and msg.get("id"):
                extraction_message_id = msg["id"]
        except Exception as exc:
            print(f"⚠️ Failed to log gsheets operation extraction_completed: {exc}")

    ops_meta: Dict[str, Any] = {
        "job_id": job_id,
        "record_count": record_count,
        "source_type": "google_sheets",
        "source_name": ctx.spreadsheet_name,
        "parent_job_id": classification.get("parent_job_id"),
        "extraction_message_id": extraction_message_id,
        "intent_state": intent_state,
    }
    if meta.get("join_key"):
        ops_meta["join_key"] = meta["join_key"]
        ops_meta["join_left_sheet"] = meta.get("join_left_sheet")
        ops_meta["join_right_sheet"] = meta.get("join_right_sheet")
    if operation_summary and operation_summary.strip():
        ops_meta["operation_summary"] = operation_summary.strip()
    if data_quality_report:
        ops_meta["data_quality_report"] = data_quality_report

    cls_for_sse = {
        "source": classification.get("source") or "google_sheets",
        "destination": classification.get("destination") or "unknown",
        "source_name": classification.get("source_name") or ctx.spreadsheet_name,
        "destination_name": classification.get("destination_name"),
    }
    job_status = await build_job_status_after_db_save(
        job_id=job_id,
        user_id=user_id,
        ops_meta=ops_meta,
        classification=cls_for_sse,
    )
    result_inner = job_status.get("result")
    if isinstance(result_inner, dict):
        if intent_state:
            result_inner["intent_state"] = intent_state
        if extraction_message_id:
            result_inner["extraction_message_id"] = extraction_message_id
        if meta.get("join_key"):
            result_inner["join_key"] = meta["join_key"]
            result_inner["join_left_sheet"] = meta.get("join_left_sheet")
            result_inner["join_right_sheet"] = meta.get("join_right_sheet")
        if meta.get("operation") == "gsheets_join":
            table_label = meta.get("join_left_sheet") and meta.get("join_right_sheet")
            if table_label:
                result_inner["table_name"] = (
                    f"{meta['join_left_sheet']} + {meta['join_right_sheet']}"
                )
        if operation_summary and operation_summary.strip():
            result_inner["operation_summary"] = operation_summary.strip()
        if data_quality_report:
            result_inner["data_quality_report"] = data_quality_report

    return job_id, extraction_job_finished_sse(job_id, job_status)
