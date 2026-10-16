"""
SSE helpers: hold HTTP stream open until extraction_jobs / sql_tasks reach a terminal state.

Mirrors server-side polling previously done by the browser via GET /job-status.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict

from utils.job_stores import sql_tasks as sql_tasks_store


def _slim_result_for_sse_wire(result: Any) -> Any:
    """Omit bulky row arrays from SSE; frontend loads rows via /extraction-results/.../records."""
    if not isinstance(result, dict):
        return result
    if result.get("data") is None:
        return result
    slim = dict(result)
    count = (
        slim.get("total_records")
        or slim.get("record_count")
        or (len(slim["data"]) if isinstance(slim.get("data"), list) else 0)
    )
    slim["data"] = None
    if count and not slim.get("record_count"):
        slim["record_count"] = count
    if count and not slim.get("total_records"):
        slim["total_records"] = count
    return slim


def job_status_payload_from_extraction_job(
    job_id: str, job: Dict[str, Any], *, slim_for_sse: bool = False
) -> Dict[str, Any]:
    """Same top-level shape as GET /job-status for extraction_jobs (Redis)."""
    result = job.get("result")
    if slim_for_sse:
        result = _slim_result_for_sse_wire(result)
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "progress": job.get("progress", 0),
        "result": result,
        "error": job.get("error"),
        "error_explanation": job.get("error_explanation"),
        "error_solution": job.get("error_solution"),
        "created_at": job.get("created_at"),
        "extraction_goals": job.get("extraction_goals", ""),
        "flow_id": job.get("flow_id"),
        "destination": job.get("destination"),
        "destination_name": job.get("destination_name"),
        "job_type": job.get("job_type"),
        "source_type": job.get("source_type"),
        "parent_job_id": job.get("parent_job_id"),
        "connection_name": job.get("connection_name"),
        "org_id": job.get("org_id"),
        "spreadsheet_name": job.get("spreadsheet_name"),
        "file_key": job.get("file_key"),
    }


async def stream_extraction_job_events_until_terminal(
    job_id: str,
    extraction_jobs,
    *,
    poll_interval: float = 0.85,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Poll Redis extraction_jobs until terminal; yield extraction_progress dicts then extraction_job_finished.

    Compatible with ChatUploader handling of Salesforce / Google Sheets / file jobs.
    """
    terminal = frozenset({"completed", "failed", "cancelled"})
    last_progress = -1
    last_emit = 0.0

    while True:
        job = await extraction_jobs.get(job_id, {}) or {}
        status = (job.get("status") or "").strip().lower() or "processing"
        now = time.monotonic()

        if status not in terminal:
            prog = int(job.get("progress") or 0)
            msg = job.get("message") or ""
            if prog != last_progress or (now - last_emit) >= 2.5:
                yield {
                    "event_type": "extraction_progress",
                    "job_id": job_id,
                    "progress": prog,
                    "message": msg,
                    "status": status,
                    "source_type": job.get("source_type"),
                }
                last_progress = prog
                last_emit = now
            await asyncio.sleep(poll_interval)
            continue

        payload = json.loads(
            json.dumps(
                job_status_payload_from_extraction_job(job_id, job, slim_for_sse=True),
                default=str,
            )
        )
        yield {"event_type": "extraction_job_finished", "job_id": job_id, "job_status": payload}
        return


def sql_job_id_from_record(task: Dict[str, Any]) -> str:
    """Redis key / wire id for SQL extractions (job_id preferred; task_id legacy)."""
    return str(task.get("job_id") or task.get("task_id") or "")


def sql_task_to_job_status_payload(job_id: str, task: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize sql_tasks Redis row to the same shape as GET /job-status for extraction_jobs."""
    return {
        "job_id": job_id,
        "status": task.get("status"),
        "progress": task.get("progress", 0),
        "result": task.get("result"),
        "error": task.get("error"),
        "error_explanation": task.get("error_explanation"),
        "error_solution": task.get("error_solution"),
        "created_at": task.get("created_at"),
        "extraction_goals": task.get("comprehensive_prompt") or task.get("extraction_goals", ""),
        "flow_id": task.get("flow_execution_id"),
        "destination": task.get("destination"),
        "destination_name": task.get("destination_name"),
        "job_type": task.get("job_type"),
        "source_type": "sql",
        "parent_job_id": task.get("parent_job_id"),
        "connector_name": task.get("connector_name"),
    }


async def stream_sql_job_events_until_terminal(
    job_id: str,
    sql_tasks=None,
    *,
    poll_interval: float = 0.85,
    max_wait_seconds: float = 1800.0,
    missing_task_grace_seconds: float = 120.0,
) -> AsyncIterator[Dict[str, Any]]:
    """Poll sql_tasks Redis until terminal; yield progress then extraction_job_finished."""
    store = sql_tasks if sql_tasks is not None else sql_tasks_store
    terminal = frozenset({"completed", "failed", "cancelled"})
    last_progress = -1
    last_emit = 0.0
    started = time.monotonic()
    saw_task_row = False

    while True:
        elapsed = time.monotonic() - started
        if elapsed > max_wait_seconds:
            yield {
                "event_type": "extraction_job_finished",
                "job_id": job_id,
                "job_status": {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "SQL extraction timed out waiting for completion.",
                    "source_type": "sql",
                },
            }
            return

        task = await store.get(job_id, {}) or {}
        if task:
            saw_task_row = True
        elif not saw_task_row and elapsed > missing_task_grace_seconds:
            yield {
                "event_type": "extraction_job_finished",
                "job_id": job_id,
                "job_status": {
                    "job_id": job_id,
                    "status": "failed",
                    "error": "SQL extraction task was not found. The job may have been interrupted.",
                    "source_type": "sql",
                },
            }
            return

        status = (task.get("status") or "").strip().lower() or "processing"
        now = time.monotonic()

        if status not in terminal:
            prog = int(task.get("progress") or 0)
            msg = task.get("message") or ""
            if prog != last_progress or (now - last_emit) >= 2.5:
                yield {
                    "event_type": "extraction_progress",
                    "job_id": job_id,
                    "progress": prog,
                    "message": msg,
                    "status": status,
                    "source_type": "sql",
                }
                last_progress = prog
                last_emit = now
            await asyncio.sleep(poll_interval)
            continue

        payload = json.loads(json.dumps(sql_task_to_job_status_payload(job_id, task), default=str))
        yield {"event_type": "extraction_job_finished", "job_id": job_id, "job_status": payload}
        return


__all__ = [
    "job_status_payload_from_extraction_job",
    "stream_extraction_job_events_until_terminal",
    "sql_job_id_from_record",
    "sql_task_to_job_status_payload",
    "stream_sql_job_events_until_terminal",
]
