"""
SSE helpers: hold HTTP stream open until migration_jobs reach a terminal state.

Mirrors extraction_sse_stream.py — used by chat migrate endpoints when conversation_id is set.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, Optional

from fastapi.responses import StreamingResponse

from utils.job_stores import migration_jobs


def migration_sse_line(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


_MIGRATION_STARTED_RESERVED = frozenset(
    {
        "event_type",
        "job_id",
        "batch_id",
        "destination",
        "destination_name",
        "record_count",
        "total_records",
        "source_job_id",
    }
)


def migration_started_extra_only(extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop keys that collide with migration_started_sse explicit parameters."""
    out: Dict[str, Any] = {}
    for key, val in (extra or {}).items():
        if key in _MIGRATION_STARTED_RESERVED or val is None:
            continue
        out[key] = val
    return out


def migration_started_sse(
    *,
    job_id: str,
    batch_id: str,
    destination: str,
    destination_name: Optional[str],
    total_records: int,
    source_job_id: Optional[str] = None,
    **extra: Any,
) -> str:
    body: Dict[str, Any] = {
        "event_type": "migration_started",
        "job_id": job_id,
        "batch_id": batch_id,
        "destination": destination,
        "destination_name": destination_name,
        "record_count": total_records,
        "total_records": total_records,
    }
    if source_job_id:
        body["source_job_id"] = source_job_id
    body.update(migration_started_extra_only(extra))
    return migration_sse_line(body)


def job_status_payload_from_migration_job(job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """Same top-level shape as GET /job-status for migration jobs."""
    total = int(job.get("total_records") or 0)
    processed = int(job.get("processed_records") or 0)
    progress = round((processed / max(total, 1)) * 100, 2) if total else 0
    status = job.get("status") or "running"
    dest = job.get("destination_type") or job.get("destination") or "salesforce"
    return {
        "job_id": job_id,
        "status": status,
        "progress": progress,
        "result": {
            "status": status,
            "total_records": total,
            "processed_records": processed,
            "successful_records": job.get("successful_records", 0),
            "failed_records": job.get("failed_records", 0),
            "batch_id": job.get("batch_id"),
            "message": job.get("error_summary"),
        },
        "error": job.get("error_summary"),
        "batch_id": job.get("batch_id"),
        "destination": dest,
        "destination_name": job.get("destination_name"),
        "destination_connector_id": job.get("destination_connector_id")
        or job.get("salesforce_token_id"),
        "source_job_id": job.get("source_job_id"),
        "instance_url": job.get("instance_url"),
        "spreadsheet_id": job.get("spreadsheet_id"),
        "job_type": "migration",
    }


_TERMINAL_CHAT_EVENT_DELAYS_SEC = (0.0, 0.15, 0.25, 0.4, 0.6, 1.0, 1.5, 2.0, 3.0)
_RECOVERY_CHAT_EVENT_DELAYS_SEC = (
    0.0,
    0.15,
    0.25,
    0.4,
    0.6,
    1.0,
    1.5,
    2.0,
    3.0,
    5.0,
    8.0,
    12.0,
    20.0,
    30.0,
)


def chat_message_sse_payload(message: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Wire shape for persisted chat rows included on migration SSE terminal events."""
    if not message or not message.get("id"):
        return None
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    created_at = message.get("created_at")
    if created_at is not None and not isinstance(created_at, str):
        created_at = str(created_at)
    return {
        "id": str(message["id"]),
        "content": message.get("content") or "",
        "created_at": created_at,
        "metadata": metadata,
    }


async def wait_for_persisted_migration_completed(
    *,
    conversation_id: str,
    user_id: str,
    job_id: str,
    batch_id: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Wait until migration_completed is persisted (post Redis terminal)."""
    from services.chat_service import ChatService

    completed: Optional[Dict[str, Any]] = None
    for delay in _TERMINAL_CHAT_EVENT_DELAYS_SEC:
        if delay:
            await asyncio.sleep(delay)
        completed = await ChatService.find_persisted_data_event(
            conversation_id,
            user_id,
            "migration_completed",
            job_id=job_id,
            batch_id=batch_id,
        )
        if completed:
            break
    return completed


async def wait_for_persisted_recovery_chat(
    *,
    conversation_id: str,
    user_id: str,
    batch_id: str,
) -> Optional[Dict[str, Any]]:
    """Wait until planner_recovery_required is persisted after recovery analysis."""
    from services.chat_service import ChatService

    recovery: Optional[Dict[str, Any]] = None
    for delay in _RECOVERY_CHAT_EVENT_DELAYS_SEC:
        if delay:
            await asyncio.sleep(delay)
        recovery = await ChatService.find_persisted_data_event(
            conversation_id,
            user_id,
            "planner_recovery_required",
            batch_id=batch_id,
        )
        if recovery:
            meta = recovery.get("metadata") or {}
            if meta.get("recovery_proposal"):
                break
            recovery = None
    return recovery


async def wait_for_persisted_migration_chat_events(
    *,
    conversation_id: str,
    user_id: str,
    job_id: str,
    batch_id: Optional[str],
    include_recovery: bool,
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Wait until backend-persisted terminal chat rows exist (post Redis terminal)."""
    completed = await wait_for_persisted_migration_completed(
        conversation_id=conversation_id,
        user_id=user_id,
        job_id=job_id,
        batch_id=batch_id,
    )
    recovery: Optional[Dict[str, Any]] = None
    if include_recovery and batch_id:
        recovery = await wait_for_persisted_recovery_chat(
            conversation_id=conversation_id,
            user_id=user_id,
            batch_id=batch_id,
        )
    return {"completed": completed, "recovery": recovery}


async def stream_migration_recovery_analysis(
    *,
    job_id: str,
    batch_id: str,
    conversation_id: str,
    user_id: str,
    event_data: Dict[str, Any],
) -> AsyncIterator[Dict[str, Any]]:
    """Run recovery LLM work on the held SSE stream and yield live activity milestones."""
    from agent.recovery_flow import maybe_offer_recovery_via_chat
    from services.migration.recovery_activity import (
        drain_recovery_activity_payloads,
        install_recovery_activity_bus,
        reset_recovery_activity_bus,
    )

    token = install_recovery_activity_bus()
    task = asyncio.create_task(
        maybe_offer_recovery_via_chat(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            batch_id=str(batch_id),
            job_id=job_id,
            event_data=event_data,
        )
    )
    try:
        while not task.done():
            for payload in drain_recovery_activity_payloads(job_id=job_id, batch_id=batch_id):
                yield payload
            await asyncio.sleep(0.08)
        await task
        for payload in drain_recovery_activity_payloads(job_id=job_id, batch_id=batch_id):
            yield payload
    finally:
        reset_recovery_activity_bus(token)


async def stream_migration_terminal_chat_events(
    *,
    job_id: str,
    conversation_id: str,
    user_id: str,
    batch_id: Optional[str],
    error_count: int,
    job_data: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    After Redis terminal: migration_completed → migration_job_finished →
    optional recovery analysis (live activity) → planner_recovery_required.
    """
    completed = await wait_for_persisted_migration_completed(
        conversation_id=conversation_id,
        user_id=user_id,
        job_id=job_id,
        batch_id=batch_id,
    )
    if completed:
        payload = chat_message_sse_payload(completed)
        if payload:
            yield {
                "event_type": "migration_completed",
                "job_id": job_id,
                "batch_id": batch_id,
                "message": payload,
            }

    job = job_data or await migration_jobs.get(job_id, {}) or {}
    payload = json.loads(json.dumps(job_status_payload_from_migration_job(job_id, job), default=str))
    yield {"event_type": "migration_job_finished", "job_id": job_id, "job_status": payload}

    if error_count <= 0 or not batch_id:
        return

    yield {
        "event_type": "recovery_analysis_started",
        "job_id": job_id,
        "batch_id": batch_id,
    }

    event_data: Dict[str, Any] = {}
    if completed:
        meta = completed.get("metadata") or {}
        if isinstance(meta, dict):
            event_data = dict(meta)
    if not event_data and job_data:
        from services.migration.completed_event_metadata import build_migration_completed_event_data

        event_data = await build_migration_completed_event_data(job_id, job, str(user_id))

    async for activity in stream_migration_recovery_analysis(
        job_id=job_id,
        batch_id=str(batch_id),
        conversation_id=conversation_id,
        user_id=user_id,
        event_data=event_data,
    ):
        yield activity

    recovery = await wait_for_persisted_recovery_chat(
        conversation_id=conversation_id,
        user_id=user_id,
        batch_id=str(batch_id),
    )
    if recovery:
        recovery_payload = chat_message_sse_payload(recovery)
        if recovery_payload:
            yield {
                "event_type": "planner_recovery_required",
                "job_id": job_id,
                "batch_id": batch_id,
                "message": recovery_payload,
            }


async def stream_migration_job_events_until_terminal(
    job_id: str,
    *,
    poll_interval: float = 0.85,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncIterator[Dict[str, Any]]:
    """
    Poll Redis migration_jobs until terminal; yield migration_progress then terminal chat events.
    When conversation_id + user_id are set, also yield persisted migration_completed chat rows.
    """
    terminal = frozenset({"completed", "failed", "cancelled"})
    last_progress_pct = -1.0
    last_emit = 0.0

    while True:
        job = await migration_jobs.get(job_id, {}) or {}
        status = (job.get("status") or "running").strip().lower()
        now = time.monotonic()

        if status not in terminal:
            total = int(job.get("total_records") or 0)
            processed = int(job.get("processed_records") or 0)
            pct = round((processed / max(total, 1)) * 100, 2) if total else 0
            msg = f"Migrating {processed:,} of {total:,} records…" if total else "Migration in progress…"
            if pct != last_progress_pct or (now - last_emit) >= 2.5:
                yield {
                    "event_type": "migration_progress",
                    "job_id": job_id,
                    "batch_id": job.get("batch_id"),
                    "progress": pct,
                    "processed_records": processed,
                    "total_records": total,
                    "successful_records": job.get("successful_records", 0),
                    "failed_records": job.get("failed_records", 0),
                    "message": msg,
                    "status": status,
                    "destination": job.get("destination_type") or job.get("destination"),
                }
                last_progress_pct = pct
                last_emit = now
            await asyncio.sleep(poll_interval)
            continue

        if conversation_id and user_id:
            batch_id = job.get("batch_id")
            error_count = int(job.get("failed_records") or 0)
            async for chat_event in stream_migration_terminal_chat_events(
                job_id=job_id,
                conversation_id=conversation_id,
                user_id=user_id,
                batch_id=batch_id,
                error_count=error_count,
                job_data=job,
            ):
                yield chat_event
        else:
            payload = json.loads(
                json.dumps(job_status_payload_from_migration_job(job_id, job), default=str)
            )
            yield {"event_type": "migration_job_finished", "job_id": job_id, "job_status": payload}
        return


async def iter_migration_job_sse_lines(
    job_id: str,
    *,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncIterator[str]:
    """Progress + terminal chat + job_finished lines for an already-started migration job."""
    async for event in stream_migration_job_events_until_terminal(
        job_id,
        conversation_id=conversation_id,
        user_id=user_id,
    ):
        yield migration_sse_line(event)


async def stream_migration_sse_after_start(
    *,
    job_id: str,
    batch_id: str,
    destination: str,
    destination_name: Optional[str],
    total_records: int,
    source_job_id: Optional[str] = None,
    sse_extra: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> AsyncIterator[str]:
    """Emit migration_started, progress milestones, terminal job_finished, and chat merge events."""
    extra = migration_started_extra_only(sse_extra)
    yield migration_started_sse(
        job_id=job_id,
        batch_id=batch_id,
        destination=destination,
        destination_name=destination_name,
        total_records=total_records,
        source_job_id=source_job_id,
        **extra,
    )
    async for event in stream_migration_job_events_until_terminal(
        job_id,
        conversation_id=conversation_id,
        user_id=user_id,
    ):
        yield migration_sse_line(event)


def migration_sse_streaming_response(
    conversation_id: Optional[str],
    *,
    job_id: str,
    batch_id: str,
    destination: str,
    destination_name: Optional[str],
    total_records: int,
    source_job_id: Optional[str] = None,
    sse_extra: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> Optional[StreamingResponse]:
    """When chat provides conversation_id, hold the HTTP connection open with migration SSE."""
    if not conversation_id:
        return None

    async def generate():
        async for line in stream_migration_sse_after_start(
            job_id=job_id,
            batch_id=batch_id,
            destination=destination,
            destination_name=destination_name,
            total_records=total_records,
            source_job_id=source_job_id,
            sse_extra=sse_extra,
            conversation_id=conversation_id,
            user_id=user_id,
        ):
            yield line

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "migration_sse_line",
    "migration_started_sse",
    "job_status_payload_from_migration_job",
    "chat_message_sse_payload",
    "wait_for_persisted_migration_completed",
    "wait_for_persisted_recovery_chat",
    "wait_for_persisted_migration_chat_events",
    "stream_migration_recovery_analysis",
    "stream_migration_terminal_chat_events",
    "iter_migration_job_sse_lines",
    "stream_migration_job_events_until_terminal",
    "stream_migration_sse_after_start",
    "migration_sse_streaming_response",
]
