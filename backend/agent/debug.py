"""Debug logging for agent runtime / migration routing (opt-in via env)."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("edison.agent_runtime")


def agent_runtime_debug_enabled() -> bool:
    v = (os.getenv("AGENT_RUNTIME_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def log_agent_runtime(label: str, payload: Optional[Dict[str, Any]] = None) -> None:
    if not agent_runtime_debug_enabled():
        return
    if payload is None:
        logger.info("[AGENT_RUNTIME_DEBUG] %s", label)
        return
    try:
        text = json.dumps(payload, default=str)[:4000]
    except Exception:
        text = str(payload)[:4000]
    logger.info("[AGENT_RUNTIME_DEBUG] %s\n%s", label, text)
