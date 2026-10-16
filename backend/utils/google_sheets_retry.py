"""
Google Sheets Retry Utility
Provides retry logic with exponential backoff for Google Sheets API calls,
specifically handling rate limit errors (429).
"""

import asyncio
from typing import Callable, Any, Optional
from services.google_sheets_service import GoogleSheetsRateLimitError


async def retry_with_backoff(
    func: Callable,
    max_retries: int = 5,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    *args,
    **kwargs
) -> Any:
    """
    Retry a function with exponential backoff, specifically handling rate limits.
    
    This function will automatically retry on GoogleSheetsRateLimitError (429 errors)
    with increasing delays between retries. It respects the Retry-After header
    from the API when available.
    
    Args:
        func: Async function to retry
        max_retries: Maximum number of retry attempts (default: 5)
        initial_delay: Initial delay in seconds before first retry (default: 1.0)
        max_delay: Maximum delay in seconds between retries (default: 60.0)
        exponential_base: Base for exponential backoff calculation (default: 2.0)
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Result from func
    
    Raises:
        GoogleSheetsRateLimitError: If rate limit persists after all retries
        Exception: Other exceptions from func (not retried)
    
    Example:
        ```python
        result = await retry_with_backoff(
            sheets_service.write_data,
            spreadsheet_id="123",
            range_name="'Sheet1'!A1",
            values=[["Hello", "World"]]
        )
        ```
    """
    delay = initial_delay
    last_exception = None
    
    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except GoogleSheetsRateLimitError as e:
            last_exception = e
            
            if attempt < max_retries:
                # Use retry_after from error if available, otherwise use exponential backoff
                wait_time = e.retry_after if e.retry_after is not None else delay
                wait_time = min(wait_time, max_delay)  # Cap at max_delay
                
                print(f"⚠️ Rate limit hit (attempt {attempt + 1}/{max_retries + 1}). Waiting {wait_time:.1f}s before retry...")
                await asyncio.sleep(wait_time)
                
                # Increase delay for next retry (exponential backoff)
                # Only if we didn't use retry_after (which is already the correct wait time)
                if e.retry_after is None:
                    delay = min(delay * exponential_base, max_delay)
            else:
                print(f"❌ Rate limit error persisted after {max_retries} retries")
                raise
        except Exception as e:
            # Don't retry on non-rate-limit errors
            # Re-raise immediately
            raise
    
    # Should never reach here, but just in case
    if last_exception:
        raise last_exception

