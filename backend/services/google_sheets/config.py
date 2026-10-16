"""Google Sheets executor v2 configuration."""

from __future__ import annotations

import os


def gsheets_chat_max_rows() -> int:
    try:
        return max(100, int(os.getenv("GSHEETS_CHAT_MAX_ROWS", "2000")))
    except ValueError:
        return 2000


def gsheets_context_cache_ttl_seconds() -> int:
    try:
        return max(60, int(os.getenv("GSHEETS_CONTEXT_CACHE_TTL", "600")))
    except ValueError:
        return 600
