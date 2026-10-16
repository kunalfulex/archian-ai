"""Periodic progress heartbeats while long-running assessment steps execute."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Dict

from services.data_assessment.progress import assessment_progress_heartbeat_seconds

ProgressBuilder = Callable[[], Dict[str, Any]]
ProgressPublisher = Callable[[Dict[str, Any]], Awaitable[None]]


@asynccontextmanager
async def assessment_progress_heartbeat(
    publish: ProgressPublisher,
    build_payload: ProgressBuilder,
):
    """
    Re-publish progress every N seconds so SSE / enterprise graph enrichment
    can detect activity during long profile_entity calls.
    """
    interval = assessment_progress_heartbeat_seconds()
    stop = asyncio.Event()

    async def _loop() -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                await publish(build_payload())

    task = asyncio.create_task(_loop(), name="assessment_progress_heartbeat")
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
