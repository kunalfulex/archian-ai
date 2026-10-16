"""Retry transient Postgres/asyncpg connection failures."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRYABLE_FRAGMENTS = (
    "connection was closed",
    "connection does not exist",
    "connection reset",
    "server closed the connection",
    "terminating connection",
    "broken pipe",
    "cannot perform operation: connection",
    "connection is closed",
)


def is_retryable_db_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(fragment in msg for fragment in _RETRYABLE_FRAGMENTS)


async def with_db_retry(
    coro_factory: Callable[[], Awaitable[T]],
    *,
    attempts: int = 2,
) -> T:
    """Run a DB coroutine once; retry once on stale connection errors."""
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return await coro_factory()
        except Exception as exc:
            last = exc
            if attempt >= attempts - 1 or not is_retryable_db_error(exc):
                raise
            logger.warning(
                "Transient DB error — reconnecting and retrying (attempt %s): %s",
                attempt + 1,
                exc,
            )
            from db.connection import database

            try:
                await database.disconnect()
            except Exception:
                pass
            try:
                await database.connect()
            except Exception:
                pass
            await asyncio.sleep(0.05)
    if last is not None:
        raise last
    raise RuntimeError("with_db_retry exhausted without result")
