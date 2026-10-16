"""Simple in-memory rate limiting for auth endpoints."""
from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import DefaultDict, List

from fastapi import HTTPException

_WINDOW_SECONDS = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_SEC", "900"))
_MAX_ATTEMPTS = int(os.getenv("AUTH_RATE_LIMIT_MAX", "10"))

_attempts: DefaultDict[str, List[float]] = defaultdict(list)


def _client_key(ip: str, action: str) -> str:
    return f"{action}:{ip or 'unknown'}"


def check_auth_rate_limit(ip: str, action: str) -> None:
    """Raise HTTP 429 when too many attempts from the same IP."""
    key = _client_key(ip, action)
    now = time.time()
    window_start = now - _WINDOW_SECONDS
    _attempts[key] = [ts for ts in _attempts[key] if ts >= window_start]
    if len(_attempts[key]) >= _MAX_ATTEMPTS:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please wait a few minutes and try again.",
        )
    _attempts[key].append(now)
