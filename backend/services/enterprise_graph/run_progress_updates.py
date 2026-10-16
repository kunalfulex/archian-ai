"""Persist graph-run progress with optional heartbeat during long rollup phases."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Optional

from services.enterprise_graph import store
from utils.job_stores import enterprise_graph_jobs

ROLLUP_HEARTBEAT_SECONDS = 2.0


async def publish_graph_run_progress(
    run_id: str,
    user_id: str,
    *,
    percent: int,
    phase: str,
    message: str,
    sub_step: Optional[str] = None,
    heartbeat: bool = False,
) -> None:
    payload: Dict[str, Any] = {
        "percent": percent,
        "phase": phase,
        "message": message,
    }
    if sub_step:
        payload["sub_step"] = sub_step
    if heartbeat:
        payload["heartbeat_at"] = datetime.now(timezone.utc).isoformat()

    await store.update_run(run_id, user_id, progress=payload)
    await enterprise_graph_jobs.update(
        run_id,
        {"status": "running", "progress": percent, "message": message},
    )


@asynccontextmanager
async def graph_rollup_heartbeat(
    run_id: str,
    user_id: str,
    *,
    phase: str,
    steps: list[tuple[int, str, str]],
) -> AsyncIterator[None]:
    """
    Cycle through (percent, sub_step, message) while a long rollup task runs.
    steps: e.g. [(93, "ai_design", "AI is designing warehouse tables…"), ...]
    """
    stop = asyncio.Event()
    idx = 0

    async def _loop() -> None:
        nonlocal idx
        while not stop.is_set():
            pct, sub_step, msg = steps[idx % len(steps)]
            await publish_graph_run_progress(
                run_id,
                user_id,
                percent=pct,
                phase=phase,
                message=msg,
                sub_step=sub_step,
                heartbeat=True,
            )
            idx += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=ROLLUP_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                pass

    task = asyncio.create_task(_loop(), name=f"graph_rollup_heartbeat:{run_id}")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
