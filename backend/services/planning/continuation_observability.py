"""Structured logs for continuation planning kernel stages."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("edison.continuation_bind")


def log_continuation_bind(
    stage: str,
    *,
    skipped: Optional[str] = None,
    job_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Emit ``[continuation_bind] stage=connector|entity|binding skipped=<reason>``.

    When ``skipped`` is set the stage was not executed (expected branch, not an error).
    """
    parts = [f"stage={stage}"]
    if skipped:
        parts.append(f"skipped={skipped}")
    if job_id:
        parts.append(f"job={job_id}")
    if extra:
        for key, val in extra.items():
            if val is not None and val != "":
                parts.append(f"{key}={val}")
    logger.info("[continuation_bind] %s", " ".join(parts))


def log_continuation_plan_scope(
    *,
    job_id: str,
    operations: Optional[list] = None,
    destination_phrase: Optional[str] = None,
    destination_same_as_source: Optional[bool] = None,
    needs_destination: Optional[bool] = None,
    data_operations: Optional[list] = None,
) -> None:
    """Log PLAN_SCOPE fields for continuation turns."""
    logger.info(
        "[continuation_plan_scope] job=%s ops=%s data_ops=%s dest_phrase=%r "
        "same_as_source=%s needs_destination=%s",
        job_id,
        operations,
        data_operations,
        (destination_phrase or "")[:120] or None,
        destination_same_as_source,
        needs_destination,
    )
