"""Schedule pipeline + label learning after migration batch completion."""
from __future__ import annotations

import logging
from typing import Optional

from db.connection import database
from services.labeling.capture_hooks import schedule_migration_label_evidence
from services.migration_records_service import MigrationRecordsService
from services.pipeline_learning.capture_hooks import schedule_migration_outcome

logger = logging.getLogger(__name__)


async def schedule_migration_learning_for_batch(
    batch_id: str,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    destination_type: Optional[str] = None,
    destination_name: Optional[str] = None,
    destination_object: Optional[str] = None,
    total_records: Optional[int] = None,
    successful_records: Optional[int] = None,
    failed_records: Optional[int] = None,
) -> None:
    """Enrich migration learning hooks with failure field/error aggregates."""
    try:
        failure_summary = await MigrationRecordsService.summarize_batch_failures(batch_id)
    except Exception as exc:
        logger.warning("summarize_batch_failures failed batch=%s: %s", batch_id, exc)
        failure_summary = {}

    if not destination_type or destination_object is None:
        try:
            row = await database.fetch_one(
                """
                SELECT destination_type, document_type
                FROM migration_batches
                WHERE id = :batch_id
                LIMIT 1
                """,
                values={"batch_id": batch_id},
            )
            if row:
                row_dict = dict(row)
                destination_type = destination_type or row_dict.get("destination_type")
                destination_object = destination_object or row_dict.get("document_type")
        except Exception:
            pass

    ok = (
        successful_records
        if successful_records is not None
        else int(failure_summary.get("successful_records") or 0)
    )
    fail = (
        failed_records
        if failed_records is not None
        else int(failure_summary.get("failed_records") or 0)
    )
    total = total_records if total_records is not None else int(
        failure_summary.get("total_records") or 0
    )
    total = max(total, ok + fail, 1)

    failed_fields = list(failure_summary.get("failed_fields") or [])
    error_samples = list(failure_summary.get("error_samples") or [])

    schedule_migration_outcome(
        user_id=str(user_id),
        conversation_id=conversation_id,
        batch_id=batch_id,
        total_records=total,
        successful_records=ok,
        failed_records=fail,
        failed_fields=failed_fields,
        error_samples=error_samples,
        destination_type=destination_type,
    )
    schedule_migration_label_evidence(
        user_id=str(user_id),
        conversation_id=conversation_id,
        batch_id=batch_id,
        destination_type=destination_type,
        destination_name=destination_name,
        total_records=total,
        successful_records=ok,
        failed_records=fail,
        destination_object=destination_object,
        failed_fields=failed_fields,
        error_samples=error_samples,
    )
