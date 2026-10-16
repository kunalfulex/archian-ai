"""
SSE streams for connector data assessment jobs and enterprise graph runs.

Server polls Postgres/Redis and pushes events — replaces browser polling.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from services.data_assessment import store as assessment_store
from services.data_assessment.api_serializers import serialize_job
from services.enterprise_graph import store as graph_store
from services.enterprise_graph.run_progress import enrich_run_with_live_progress
from utils.job_stores import assessment_jobs, enterprise_graph_jobs


def assessment_sse_poll_interval() -> float:
    try:
        return max(0.25, float(os.getenv("ASSESSMENT_SSE_POLL_INTERVAL", "1.0")))
    except ValueError:
        return 1.0


def assessment_sse_line(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _job_fingerprint(job: Dict[str, Any]) -> Tuple[Any, ...]:
    prog = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    redis_j = job.get("redis") if isinstance(job.get("redis"), dict) else {}
    return (
        job.get("status"),
        prog.get("percent"),
        prog.get("sub_phase"),
        prog.get("completed_entities"),
        prog.get("total_entities"),
        prog.get("current_entity"),
        prog.get("heartbeat_at"),
        redis_j.get("message"),
    )


def _run_fingerprint(run: Dict[str, Any]) -> Tuple[Any, ...]:
    prog = run.get("progress") if isinstance(run.get("progress"), dict) else {}
    members = run.get("member_jobs") or []
    member_fp = tuple(
        (
            m.get("status"),
            m.get("display_status"),
            (m.get("assessment_progress") or {}).get("percent"),
            (m.get("assessment_progress") or {}).get("sub_phase"),
            (m.get("assessment_progress") or {}).get("completed_entities"),
            (m.get("assessment_progress") or {}).get("current_entity"),
            (m.get("assessment_progress") or {}).get("heartbeat_at"),
        )
        for m in members
    )
    return (
        run.get("status"),
        prog.get("percent"),
        prog.get("message"),
        prog.get("phase"),
        prog.get("sub_step"),
        prog.get("heartbeat_at"),
        prog.get("detail"),
        member_fp,
    )


async def build_assessment_job_payload(job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await assessment_store.get_job(job_id, user_id)
    if not row:
        return None
    redis_j = await assessment_jobs.get(job_id, {})
    return {**serialize_job(dict(row)), "redis": redis_j}


async def stream_assessment_job_events_until_terminal(
    job_id: str,
    user_id: str,
    *,
    poll_interval: Optional[float] = None,
) -> AsyncIterator[str]:
    """Yield SSE lines until assessment job reaches a terminal status."""
    poll = poll_interval if poll_interval is not None else assessment_sse_poll_interval()
    terminal = frozenset({"completed", "failed", "cancelled"})
    last_fp: Optional[Tuple[Any, ...]] = None
    last_heartbeat = 0.0

    while True:
        job = await build_assessment_job_payload(job_id, user_id)
        if not job:
            yield assessment_sse_line(
                {
                    "event_type": "assessment_job_finished",
                    "job_id": job_id,
                    "job": None,
                    "error": "Job not found",
                }
            )
            return

        status = (job.get("status") or "").strip().lower()
        now = time.monotonic()

        if status not in terminal:
            fp = _job_fingerprint(job)
            if fp != last_fp or (now - last_heartbeat) >= 2.5:
                yield assessment_sse_line(
                    {
                        "event_type": "assessment_progress",
                        "job_id": job_id,
                        "job": job,
                    }
                )
                last_fp = fp
                last_heartbeat = now
            await asyncio.sleep(poll)
            continue

        yield assessment_sse_line(
            {
                "event_type": "assessment_job_finished",
                "job_id": job_id,
                "job": job,
            }
        )
        return


async def build_enterprise_graph_run_payload(run_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    run = await graph_store.get_run(run_id, user_id)
    if not run:
        return None
    run = await enrich_run_with_live_progress(run, user_id)
    redis_j = await enterprise_graph_jobs.get(run_id, {})
    return {**run, "redis": redis_j}


async def stream_enterprise_graph_run_events_until_terminal(
    run_id: str,
    user_id: str,
    *,
    poll_interval: Optional[float] = None,
) -> AsyncIterator[str]:
    """Yield SSE lines until enterprise graph run reaches a terminal status."""
    poll = poll_interval if poll_interval is not None else assessment_sse_poll_interval()
    terminal = frozenset({"completed", "failed", "cancelled"})
    last_fp: Optional[Tuple[Any, ...]] = None
    last_heartbeat = 0.0

    while True:
        run = await build_enterprise_graph_run_payload(run_id, user_id)
        if not run:
            yield assessment_sse_line(
                {
                    "event_type": "enterprise_graph_run_finished",
                    "run_id": run_id,
                    "run": None,
                    "error": "Run not found",
                }
            )
            return

        status = (run.get("status") or "").strip().lower()
        now = time.monotonic()

        if status not in terminal:
            fp = _run_fingerprint(run)
            if fp != last_fp or (now - last_heartbeat) >= 2.5:
                yield assessment_sse_line(
                    {
                        "event_type": "enterprise_graph_run_progress",
                        "run_id": run_id,
                        "run": run,
                    }
                )
                last_fp = fp
                last_heartbeat = now
            await asyncio.sleep(poll)
            continue

        yield assessment_sse_line(
            {
                "event_type": "enterprise_graph_run_finished",
                "run_id": run_id,
                "run": run,
            }
        )
        return
