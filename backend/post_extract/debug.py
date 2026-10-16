"""
Opt-in verbose logging for post-extract (rules proposal → merge → apply).

Set ``POST_EXTRACT_DEBUG=true`` in the environment to enable.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger("post_extract")


def post_extract_debug_enabled() -> bool:
    v = (os.getenv("POST_EXTRACT_DEBUG") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def log_snapshot(label: str, payload: Any, *, max_chars: int = 12000) -> None:
    if not post_extract_debug_enabled():
        return
    try:
        text = json.dumps(payload, default=str, indent=2, ensure_ascii=False)
    except Exception:
        text = str(payload)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n... [truncated]"
    logger.info("[POST_EXTRACT_DEBUG] %s\n%s", label, text)
