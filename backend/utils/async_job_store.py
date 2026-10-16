"""
Async-compatible job store wrapper for backward compatibility.
Provides a dictionary-like interface that works with async Redis backend.
"""

import asyncio
from typing import Dict, Any, Optional
from utils.redis_service import RedisJobStore


class AsyncJobStore:
    """
    Async-compatible wrapper around RedisJobStore that provides
    a more convenient interface for async code.
    
    This wrapper handles the async nature of Redis operations
    while providing cleaner access patterns.
    
    Uses per-job locks to avoid blocking unrelated job updates,
    improving throughput when multiple users/jobs run concurrently.
    """
    
    _MAX_JOB_LOCKS = 5000  # Evict oldest when exceeded to prevent unbounded growth
    
    def __init__(self, job_store: RedisJobStore):
        """
        Initialize async job store wrapper
        
        Args:
            job_store: Underlying RedisJobStore instance
        """
        self.job_store = job_store
        self._job_locks: Dict[str, asyncio.Lock] = {}
        self._locks_lock = asyncio.Lock()  # Protects _job_locks dict
    
    async def _get_job_lock(self, job_id: str) -> asyncio.Lock:
        """Get or create a per-job lock. Evicts oldest locks if over limit."""
        async with self._locks_lock:
            if job_id not in self._job_locks:
                if len(self._job_locks) >= self._MAX_JOB_LOCKS:
                    to_remove = list(self._job_locks.keys())[: self._MAX_JOB_LOCKS // 2]
                    for k in to_remove:
                        del self._job_locks[k]
                self._job_locks[job_id] = asyncio.Lock()
            return self._job_locks[job_id]
    
    async def get(self, job_id: str, default: Any = None) -> Any:
        """
        Get job data
        
        Args:
            job_id: Job identifier
            default: Default value if job not found
        
        Returns:
            Job data or default
        """
        job = await self.job_store.get_job(job_id)
        return job if job is not None else default
    
    async def set(self, job_id: str, job_data: Dict[str, Any]) -> bool:
        """
        Set job data
        
        Args:
            job_id: Job identifier
            job_data: Job data dictionary
        
        Returns:
            True if successful
        """
        return await self.job_store.set_job(job_id, job_data)
    
    async def update(self, job_id: str, updates: Dict[str, Any]) -> bool:
        """
        Update job data (merge with existing)
        
        Args:
            job_id: Job identifier
            updates: Dictionary of updates to merge
        
        Returns:
            True if successful
        """
        lock = await self._get_job_lock(job_id)
        async with lock:
            return await self.job_store.update_job(job_id, updates)
    
    async def exists(self, job_id: str) -> bool:
        """
        Check if job exists
        
        Args:
            job_id: Job identifier
        
        Returns:
            True if job exists
        """
        return await self.job_store.job_exists(job_id)
    
    async def delete(self, job_id: str) -> bool:
        """
        Delete job
        
        Args:
            job_id: Job identifier
        
        Returns:
            True if successful
        """
        return await self.job_store.delete_job(job_id)


def create_sync_progress_callback(async_store: AsyncJobStore, job_id: str):
    """
    Create a synchronous progress callback that updates the async job store.
    This uses asyncio.create_task to handle the async update from sync context.
    
    WARNING: This creates a fire-and-forget task. If you need to ensure
    the update completes, use an async callback instead.
    
    Args:
        async_store: AsyncJobStore instance
        job_id: Job identifier
    
    Returns:
        Sync callback function that can be passed to synchronous code
    """
    def progress_callback(progress_percent: int, message: str = ""):
        """Synchronous progress callback"""
        # Schedule async update (fire-and-forget)
        # Get the event loop (or create one if needed)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Loop is already running, schedule the task
                asyncio.create_task(async_store.update(job_id, {"progress": progress_percent}))
            else:
                # No running loop, run it directly
                loop.run_until_complete(async_store.update(job_id, {"progress": progress_percent}))
        except RuntimeError:
            # No event loop, create a new one (unlikely in FastAPI context)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(async_store.update(job_id, {"progress": progress_percent}))
            loop.close()
    
    return progress_callback


def create_async_progress_callback(async_store: AsyncJobStore, job_id: str):
    """
    Create an async progress callback for use in async functions.
    
    Args:
        async_store: AsyncJobStore instance
        job_id: Job identifier
    
    Returns:
        Async callback function
    """
    async def progress_callback(progress_percent: int, message: str = ""):
        """Async progress callback"""
        await async_store.update(job_id, {"progress": progress_percent})
    
    return progress_callback
