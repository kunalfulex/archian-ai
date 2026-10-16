"""
Monitoring utilities for high-volume extraction and migration.
Logs Redis usage, job size, and duration for observability.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# Set to "true" or "1" to enable detailed extraction metrics logging
MONITORING_ENABLED = os.getenv("EXTRACTION_MONITORING_ENABLED", "false").lower() in ("true", "1", "yes")


def log_redis_slim_metrics(
    job_id: str,
    record_count: int,
    slimmed: bool,
    reason: str = "",
) -> None:
    """Log when Redis result is slimmed (data not stored)."""
    if not MONITORING_ENABLED:
        return
    print(
        f"[REDIS_SLIM_METRICS] job_id={job_id} record_count={record_count} "
        f"slimmed={slimmed} reason={reason or 'N/A'}"
    )
