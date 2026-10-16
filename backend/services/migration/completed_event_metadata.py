"""Build migration_completed chat metadata with save-flow fields."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional


def resolve_destination_connector_from_job(job_data: Dict[str, Any]) -> Optional[str]:
    """Destination id for save-flow: SF token, SQL connector, or spreadsheet id."""
    dest = (
        (job_data.get("destination") or job_data.get("destination_type") or "salesforce")
        .strip()
        .lower()
    )
    if dest in ("google_sheets", "gsheets"):
        sid = job_data.get("spreadsheet_id") or job_data.get("destination_spreadsheet_id")
        return str(sid) if sid else None
    if dest == "sql":
        cid = job_data.get("destination_connector_id") or job_data.get("connector_id")
        return str(cid) if cid else None
    # salesforce (default)
    token = job_data.get("destination_connector_id") or job_data.get("salesforce_token_id")
    return str(token) if token else None


async def log_migration_completed_chat_event(job_id: str) -> None:
    """Log migration_completed to chat when job has conversation_id (all destination types)."""
    try:
        from utils.job_stores import migration_jobs
        from services.chat_service import ChatService

        job_data = await migration_jobs.get(job_id) or {}
        if not job_data:
            return
        conversation_id = job_data.get("conversation_id")
        user_id = job_data.get("user_id")
        if not conversation_id or not user_id:
            return
        event_data = await build_migration_completed_event_data(job_id, job_data, str(user_id))
        batch_id = event_data.get("batch_id")
        error_count = int(event_data.get("error_count") or 0)

        await ChatService.log_data_event(
            conversation_id=str(conversation_id),
            user_id=str(user_id),
            event_type="migration_completed",
            event_data=event_data,
        )

        # Chat migrations hold SSE open and run recovery with live activity milestones there.
        defer_recovery_to_sse = bool(conversation_id)
        if batch_id and error_count > 0 and not defer_recovery_to_sse:
            from agent.recovery_flow import maybe_offer_recovery_via_chat

            await maybe_offer_recovery_via_chat(
                user_id=str(user_id),
                conversation_id=str(conversation_id),
                batch_id=str(batch_id),
                job_id=job_id,
                event_data=event_data,
            )

        source_job_id = job_data.get("source_job_id")
        status = (job_data.get("status") or "completed").strip().lower()
        if source_job_id and status in ("completed", "success"):
            try:
                from services.extraction_results_service import ExtractionResultsService

                await ExtractionResultsService.update_pipeline_stage(
                    str(source_job_id),
                    str(user_id),
                    "migrated",
                )
            except Exception as stage_exc:
                print(
                    f"⚠️ Failed to set pipeline_stage=migrated for {source_job_id}: {stage_exc}"
                )

        if batch_id:
            await _maybe_capture_recovery_success(
                user_id=str(user_id),
                conversation_id=str(conversation_id),
                event_data=event_data,
            )
    except Exception as exc:
        print(f"⚠️ Failed to log migration_completed event for {job_id}: {exc}")


async def _maybe_capture_recovery_success(
    *,
    user_id: str,
    conversation_id: str,
    event_data: Dict[str, Any],
) -> None:
    """Record pipeline learning when a recovery retry migration succeeds."""
    from agent.recovery_policy import MIN_SUCCESS_RATE_OK

    success = int(event_data.get("success_count") or 0)
    errors = int(event_data.get("error_count") or 0)
    total = success + errors
    if total <= 0:
        return
    rate = success / total
    if rate < MIN_SUCCESS_RATE_OK:
        return

    try:
        from services.chat_service import ChatService
        from services.pipeline_learning.service import PipelineLearningService

        messages = await ChatService.get_messages(conversation_id, user_id, limit=50)
        prior_batch_id = None
        for msg in reversed(messages or []):
            meta = msg.get("metadata") or {}
            if meta.get("event_type") == "recovery_approved":
                prior_batch_id = meta.get("recovery_batch_id")
                break
        if not prior_batch_id:
            return
        if PipelineLearningService.enabled():
            PipelineLearningService.schedule_capture(
                lambda: PipelineLearningService.capture_recovery_success(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    batch_id=str(event_data.get("batch_id") or ""),
                    success_rate=rate,
                    prior_batch_id=prior_batch_id,
                )
            )
    except Exception as exc:
        print(f"⚠️ recovery success capture skipped: {exc}")


async def build_migration_completed_event_data(
    job_id: str,
    job_data: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    """
  Merge terminal job fields with extraction context so save-flow works on page reload
  without relying on the frontend poll path.
    """
    status = job_data.get("status", "completed")
    destination = job_data.get("destination") or "salesforce"
    success_count = job_data.get("successful_records", 0)
    error_count = job_data.get("failed_records", 0)

    dest_connector = resolve_destination_connector_from_job(job_data)
    dest_norm = (destination or "").strip().lower()
    if dest_norm in ("google_sheets", "gsheets"):
        destination = "google_sheets"
    elif dest_norm == "sql":
        destination = "sql"

    event_data: Dict[str, Any] = {
        "job_id": job_id,
        "batch_id": job_data.get("batch_id"),
        "destination": destination,
        "destination_name": job_data.get("destination_name"),
        "destination_connector_id": dest_connector,
        "instance_url": job_data.get("instance_url"),
        "spreadsheet_id": job_data.get("spreadsheet_id"),
        "destination_spreadsheet_id": job_data.get("spreadsheet_id")
        or job_data.get("destination_spreadsheet_id"),
        "destination_sheet_name": job_data.get("sheet_name"),
        "destination_table_name": job_data.get("table_name"),
        "success_count": success_count,
        "error_count": error_count,
        "status": "success" if status in ("completed", "success") else status,
        "source_job_id": job_data.get("source_job_id"),
    }

    if error_count > 0 and job_data.get("batch_id"):
        try:
            from services.migration.failure_summary import build_failure_summary_for_batch

            failure_summary = await build_failure_summary_for_batch(str(job_data["batch_id"]))
            if failure_summary:
                event_data["failure_summary"] = failure_summary
                event_data["status"] = "partial_success"
        except Exception as exc:
            print(f"⚠️ failure_summary skipped for {job_id}: {exc}")

    if status == "failed":
        event_data["status"] = "failed"
        event_data["error"] = job_data.get("error_summary", "Unknown error")

    source_job_id = job_data.get("source_job_id")
    if not source_job_id or not user_id:
        return event_data

    try:
        from services.extraction_results_service import ExtractionResultsService

        row = await ExtractionResultsService.get_extraction_result(source_job_id, user_id)
        if not row:
            return event_data

        source_type = (row.get("source_type") or "").lower()
        event_data["source_type"] = source_type
        if source_type == "sql":
            event_data["extracted_schema_type"] = "sql"
        elif source_type in ("google_sheets", "gsheets"):
            event_data["extracted_schema_type"] = "gsheets"
        elif source_type == "salesforce":
            event_data["extracted_schema_type"] = "salesforce"
        elif source_type == "file":
            event_data["extracted_schema_type"] = "file"

        event_data["source_name"] = row.get("source_name")
        event_data["document_type"] = row.get("document_type")
        event_data["document_description"] = row.get("document_description")
        event_data["user_prompt"] = row.get("extraction_goals")

        src_id = row.get("source_identifier")
        if src_id:
            event_data["source_connector_id"] = str(src_id)
            event_data["connector_id"] = str(src_id)

        meta = row.get("extraction_metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                meta = {}
        if isinstance(meta, dict):
            sql_q = meta.get("sql_query") or meta.get("soql") or meta.get("query")
            if sql_q:
                event_data["sql_query"] = sql_q
            if meta.get("table_name"):
                event_data["table_name"] = meta["table_name"]
            if meta.get("source_table"):
                event_data["table_name"] = event_data.get("table_name") or meta["source_table"]
            if meta.get("file_key"):
                event_data["file_key"] = meta["file_key"]
            if meta.get("spreadsheet_id") and not event_data.get("spreadsheet_id"):
                event_data["spreadsheet_id"] = meta["spreadsheet_id"]
            rules = meta.get("extraction_rules")
            if rules and isinstance(rules, dict):
                event_data["extraction_rules"] = rules

        extracted = row.get("extracted_data") or []
        if isinstance(extracted, list) and extracted and isinstance(extracted[0], dict):
            event_data["extracted_schema"] = extracted[0]
    except Exception as exc:
        print(f"⚠️ build_migration_completed_event_data enrichment skipped: {exc}")

    return event_data
