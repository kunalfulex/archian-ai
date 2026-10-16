"""Build compact failure summaries for migration_completed events and job status."""
from __future__ import annotations

from typing import Any, Dict, Optional


async def build_failure_summary_for_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    """Aggregate failed-record errors and classification for UI + chat metadata."""
    if not batch_id:
        return None

    from services.migration_records_service import MigrationRecordsService
    from services.planning.recovery_error_classifier import classify_failed_records

    summary = await MigrationRecordsService.summarize_batch_failures(batch_id)
    if not summary or int(summary.get("failed_records") or 0) <= 0:
        return None

    rows = await MigrationRecordsService.get_failed_records_for_recovery(batch_id)
    classification = classify_failed_records(rows, batch_id=str(batch_id))

    return {
        "failed_count": int(summary.get("failed_records") or 0),
        "successful_count": int(summary.get("successful_records") or 0),
        "total_records": int(summary.get("total_records") or 0),
        "success_rate": float(summary.get("success_rate") or 0.0),
        "top_errors": summary.get("top_errors") or [],
        "failed_fields": summary.get("failed_fields") or [],
        "error_samples": summary.get("error_samples") or [],
        "recovery_action": classification.recovery_action,
        "recoverable_count": classification.recoverable_count,
        "unrecoverable_count": classification.unrecoverable_count,
        "unrecoverable_notice": classification.unrecoverable_notice,
        "clusters": classification.clusters,
    }
