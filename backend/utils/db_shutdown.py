"""Detect app shutdown / DB pool teardown (e.g. uvicorn --reload)."""
from __future__ import annotations

_shutting_down = False


def mark_db_shutting_down() -> None:
    global _shutting_down
    _shutting_down = True


def is_db_shutting_down() -> bool:
    return _shutting_down


def is_pool_closing_error(exc: BaseException) -> bool:
    if is_db_shutting_down():
        return True
    msg = str(exc).lower()
    return "pool is closing" in msg or "connection is closed" in msg
