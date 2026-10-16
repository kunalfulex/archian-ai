"""Opt-in debug logging for Salesforce extract → SOQL → plan execution."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("edison.sf_extract")

# Set SF_EXTRACT_DEBUG=1 in .env to trace full extract pipeline (default off).
# Abort/failure paths always log at WARNING regardless of this flag.
_VERBOSE = None


def sf_extract_debug_enabled() -> bool:
    global _VERBOSE
    if _VERBOSE is None:
        _VERBOSE = (os.getenv("SF_EXTRACT_DEBUG") or "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    return bool(_VERBOSE)


def _serialize(fields: Dict[str, Any], *, max_len: int = 3500) -> str:
    try:
        text = json.dumps(fields, default=str, ensure_ascii=False)
    except Exception:
        text = str(fields)
    if len(text) > max_len:
        return text[: max_len - 20] + "… [truncated]"
    return text


def log_sf_extract(
    phase: str,
    *,
    level: str = "info",
    always: bool = False,
    **fields: Any,
) -> None:
    """
    Structured Salesforce extract debug line.

    phase: short stage id (e.g. extract_start, soql_llm_failed)
    always: log even when SF_EXTRACT_DEBUG is off (use for abort/error paths)
    """
    if not always and not sf_extract_debug_enabled():
        return
    payload = {"phase": phase, **fields}
    msg = f"[SF_EXTRACT] {phase} {_serialize(payload)}"
    if level == "warning":
        logger.warning(msg)
    elif level == "error":
        logger.error(msg)
    else:
        logger.info(msg)
    # Mirror to stdout for local uvicorn terminals (grep: SF_EXTRACT)
    print(msg, flush=True)


def approx_token_count(text: Optional[str]) -> int:
    """Rough token estimate (~4 chars per token) for prompt sizing."""
    if not text:
        return 0
    return max(1, len(text) // 4)


class SfExtractTimer:
    """Context helper for timed phases."""

    def __init__(self, phase: str, **fields: Any):
        self.phase = phase
        self.fields = fields
        self._t0 = 0.0

    def __enter__(self) -> "SfExtractTimer":
        self._t0 = time.monotonic()
        log_sf_extract(f"{self.phase}_start", **self.fields)
        return self

    def __exit__(self, exc_type, exc, _tb) -> None:
        elapsed_ms = int((time.monotonic() - self._t0) * 1000)
        if exc_type is not None:
            log_sf_extract(
                f"{self.phase}_error",
                level="warning",
                always=True,
                elapsed_ms=elapsed_ms,
                error_type=exc_type.__name__ if exc_type else None,
                error=str(exc)[:500] if exc else None,
                **self.fields,
            )
        else:
            log_sf_extract(f"{self.phase}_done", elapsed_ms=elapsed_ms, **self.fields)
