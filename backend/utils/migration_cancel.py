"""
Shared utilities for migration cancellation support.
Enables background migration jobs to check if the user has cancelled and stop promptly.
"""
from typing import Awaitable, Callable, Optional

from utils.async_job_store import AsyncJobStore


class MigrationCancelledError(Exception):
    """Raised when user cancels migration mid-flight."""
    pass


async def is_migration_cancelled(job_id: str, job_store: AsyncJobStore) -> bool:
    """
    Check if the migration job has been cancelled by the user.

    Args:
        job_id: Migration job ID (from migration_jobs Redis store)
        job_store: AsyncJobStore instance (e.g. migration_job_store)

    Returns:
        True if job has been cancelled, False otherwise
    """
    job_data = await job_store.get(job_id)
    return bool(job_data and job_data.get("cancelled"))


def make_cancel_check(
    job_id: str,
    job_store: AsyncJobStore,
) -> Callable[[], Awaitable[bool]]:
    """
    Create an async callable that checks if the migration is cancelled.
    Used to pass into bulk API wait functions that expect a cancel_check callback.

    Args:
        job_id: Migration job ID
        job_store: AsyncJobStore instance

    Returns:
        Async callable that returns True if cancelled, False otherwise
    """

    async def _check() -> bool:
        return await is_migration_cancelled(job_id, job_store)

    return _check
