"""
Global task concurrency limiter for background tasks.
Prevents resource exhaustion when many background tasks are created simultaneously.
"""

import asyncio
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Maximum number of concurrent background tasks
# This prevents overwhelming the event loop and database connection pool
# Recommended: 50-100 based on your DB pool size (we use 75 to leave room for API requests)
MAX_CONCURRENT_BACKGROUND_TASKS = int(os.getenv("MAX_CONCURRENT_BACKGROUND_TASKS", "75"))

# Global semaphore to limit concurrent background tasks
_global_task_semaphore: Optional[asyncio.Semaphore] = None


def get_global_task_semaphore() -> asyncio.Semaphore:
    """Get or create the global task semaphore"""
    global _global_task_semaphore
    if _global_task_semaphore is None:
        _global_task_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BACKGROUND_TASKS)
    return _global_task_semaphore


async def create_limited_task(coro):
    """
    Create a background task with global concurrency limiting.
    
    This ensures that even if 100+ users trigger background tasks simultaneously,
    we won't create more than MAX_CONCURRENT_BACKGROUND_TASKS concurrent tasks.
    Additional tasks will queue and execute as slots become available.
    
    Args:
        coro: Coroutine to execute as a background task
    
    Returns:
        asyncio.Task object
    """
    semaphore = get_global_task_semaphore()
    
    async def limited_coro():
        """Wrapper that acquires semaphore before executing the coroutine"""
        async with semaphore:
            return await coro
    
    return asyncio.create_task(limited_coro())


def reset_global_task_semaphore():
    """Reset the global semaphore (useful for testing)"""
    global _global_task_semaphore
    _global_task_semaphore = None
