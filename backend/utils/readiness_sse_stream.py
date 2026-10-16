"""SSE stream for connector agent readiness (metadata + labeling + profiling)."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any, AsyncIterator, Dict, Optional, Tuple

from services.labeling.readiness_service import get_readiness_state
from services.labeling.connector_ref import normalize_connector_ref, connector_ref_from_instance_id

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def readiness_sse_poll_interval() -> float:
    try:
        return max(0.5, float(os.getenv("READINESS_SSE_POLL_INTERVAL", "2.0")))
    except ValueError:
        return 2.0


def readiness_sse_line(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _readiness_fingerprint(state: Dict[str, Any]) -> Tuple[Any, ...]:
    stats = state.get("entity_labeling_stats") or {}
    meta = state.get("metadata_sync_progress") or {}
    return (
        state.get("phase"),
        state.get("status"),
        state.get("is_ready"),
        stats.get("ready"),
        stats.get("total"),
        stats.get("pending"),
        stats.get("processing"),
        state.get("profile_status"),
        state.get("metadata_sync_complete"),
        state.get("metadata_sync_in_progress"),
        meta.get("processed"),
        meta.get("total"),
        meta.get("percent"),
    )


async def stream_connector_readiness_events_until_terminal(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    poll_interval: Optional[float] = None,
) -> AsyncIterator[str]:
    poll = poll_interval if poll_interval is not None else readiness_sse_poll_interval()
    ref = await normalize_connector_ref(
        user_id,
        connector_type,
        connector_ref_from_instance_id(connector_type, connector_instance_id),
    )
    last_fp: Optional[Tuple[Any, ...]] = None

    while True:
        state = await get_readiness_state(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=connector_instance_id,
            connector_ref=ref,
        )
        fp = _readiness_fingerprint(state)
        if fp != last_fp:
            last_fp = fp
            yield readiness_sse_line({"event_type": "connector_readiness", **state})

        if state.get("is_ready"):
            yield readiness_sse_line({"event_type": "connector_readiness_finished", **state})
            break

        await asyncio.sleep(poll)
