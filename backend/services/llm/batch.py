"""Concurrency-limited batch execution for multi-LLM workloads."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def map_with_concurrency(
    items: List[T],
    fn: Callable[[T], Awaitable[R]],
    *,
    max_concurrent: int = 5,
) -> List[R]:
    if not items:
        return []
    sem = asyncio.Semaphore(max(1, max_concurrent))

    async def _run(item: T) -> R:
        async with sem:
            return await fn(item)

    return await asyncio.gather(*[_run(i) for i in items])


async def process_in_batches(
    items: List[T],
    fn: Callable[[List[T]], Awaitable[None]],
    *,
    batch_size: int = 5,
) -> None:
    size = max(1, batch_size)
    for i in range(0, len(items), size):
        chunk = items[i : i + size]
        await fn(chunk)
