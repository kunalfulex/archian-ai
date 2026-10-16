import asyncio
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

from services.database_migration_service import DatabaseMigrationService
from services.migration_records_service import MigrationRecordsService
from utils.redis_service import migration_job_store
from utils.async_job_store import AsyncJobStore


class DatabaseBackgroundMigrationService:
    """Background processor for SQL database migrations. Uses Redis for job storage (same as Salesforce/Google Sheets) to enable cancel support."""

    def __init__(self):
        self.job_store = AsyncJobStore(migration_job_store)
        self.milestone_notifications: Dict[str, List[int]] = {}

    async def start_background_migration(
        self,
        *,
        user_id: str,
        mapping: Dict[str, Any],
        connector_id: str,
        table_name: str,
        document_type: str,
        operation: str,
        batch_id: Optional[str],
        destination_name: Optional[str],
        batch_size: int = 1000,
        source_job_id: Optional[str] = None,
        total_records: Optional[int] = None,
        transformation_rules: Optional[Dict[str, Any]] = None,
        is_transformed_data: bool = False,
        migration_rules: Optional[Dict[str, Any]] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        if not source_job_id:
            raise ValueError("source_job_id is required for migration")
        if total_records is None or total_records < 0:
            raise ValueError("total_records is required when using source_job_id")

        job_id = f"sql_{uuid.uuid4()}"

        job_data = {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "queued",
            "total_records": total_records,
            "processed_records": 0,
            "successful_records": 0,
            "failed_records": 0,
            "started_at": None,
            "completed_at": None,
            "error_summary": None,
            "operation": operation,
            "connector_id": connector_id,
            "destination_connector_id": str(connector_id) if connector_id else None,
            "table_name": table_name,
            "destination": "sql",
            "destination_type": "sql",
            "destination_name": destination_name or table_name,
            "source_job_id": source_job_id,
            "conversation_id": conversation_id,
        }
        await self.job_store.set(job_id, job_data)
        self.milestone_notifications[job_id] = []

        asyncio.create_task(
            self._process_job(
                job_id=job_id,
                user_id=user_id,
                mapping=mapping,
                connector_id=connector_id,
                table_name=table_name,
                document_type=document_type,
                operation=operation,
                batch_id=batch_id,
                destination_name=destination_name,
                batch_size=batch_size,
                source_job_id=source_job_id,
                transformation_rules=transformation_rules,
                is_transformed_data=is_transformed_data,
                migration_rules=migration_rules,
            )
        )

        return job_id

    async def _process_job(
        self,
        job_id: str,
        user_id: str,
        mapping: Dict[str, Any],
        connector_id: str,
        table_name: str,
        document_type: str,
        operation: str,
        batch_id: Optional[str],
        destination_name: Optional[str],
        batch_size: int,
        source_job_id: str,
        transformation_rules: Optional[Dict[str, Any]] = None,
        is_transformed_data: bool = False,
        migration_rules: Optional[Dict[str, Any]] = None,
    ):
        from services.extraction_results_service import ExtractionResultsService
        from utils.missing_field_preference import apply_missing_field_preference

        await self.job_store.update(job_id, {
            "status": "running",
            "started_at": datetime.utcnow().isoformat(),
        })
        job_info = await self.job_store.get(job_id, {})
        total_records = job_info.get("total_records", 0)
        successful = 0
        failed = 0
        start_index = 0

        milestones = {
            25: max(1, int(total_records * 0.25)),
            50: max(1, int(total_records * 0.50)),
            75: max(1, int(total_records * 0.75)),
            100: total_records,
        }

        service = DatabaseMigrationService()
        current_mapping = mapping

        try:
            ready = await ExtractionResultsService.wait_for_extraction_records_persisted(
                source_job_id, user_id, timeout_seconds=600.0
            )
            if not ready:
                if await ExtractionResultsService.is_extraction_detail_expired(source_job_id, user_id):
                    err = ExtractionResultsService.MIGRATION_DETAIL_EXPIRED_MESSAGE
                else:
                    err = (
                        "Extraction data was not fully persisted before migration "
                        "(timed out waiting for stored rows). Try again."
                    )
                await self.job_store.update(job_id, {
                    "status": "failed",
                    "completed_at": datetime.utcnow().isoformat(),
                    "error_summary": err,
                })
                if batch_id:
                    await MigrationRecordsService.update_batch_status(
                        batch_id=batch_id,
                        status="failed",
                        error_summary=err,
                    )
                return

            async for chunk in ExtractionResultsService.iterate_extraction_records(
                job_id=source_job_id,
                user_id=user_id,
                batch_size=batch_size,
            ):
                # Check for user-initiated cancellation
                job_data = await self.job_store.get(job_id, {})
                if job_data and job_data.get("cancelled"):
                    print(f"🛑 SQL migration job {job_id} cancelled by user")
                    await self.job_store.update(job_id, {
                        "status": "cancelled",
                        "completed_at": datetime.utcnow().isoformat(),
                        "processed_records": start_index,
                        "successful_records": successful,
                        "failed_records": failed,
                    })
                    if batch_id:
                        await MigrationRecordsService.update_batch_status(
                            batch_id=batch_id,
                            status="cancelled",
                            successful_count=successful,
                            failed_count=failed,
                            error_summary="Cancelled by user",
                        )
                    return

                if not chunk:
                    continue

                # Apply mapping-stage rules at migrate time only when rows are not pre-transformed.
                from services.migration.transform_state import (
                    should_apply_runtime_migration_transform,
                )

                if should_apply_runtime_migration_transform(
                    is_transformed_data=is_transformed_data,
                    transformation_rules=transformation_rules,
                ):
                    try:
                        from services.data_transformation_service import DataTransformationService
                        transformed_data, _, transformed_mapping = await DataTransformationService.transform_data_for_migration(
                            data=chunk,
                            mapping=current_mapping,
                            transformation_rules=transformation_rules,
                            user_id=user_id,
                            org_id=None,
                            destination="sql",
                        )
                        chunk = transformed_data
                        current_mapping = transformed_mapping
                    except Exception as e:
                        failed += len(chunk)
                        start_index += len(chunk)
                        await self.job_store.update(job_id, {
                            "processed_records": start_index,
                            "failed_records": failed,
                        })
                        continue

                raw_batch_len = len(chunk)
                chunk, skipped_in_batch = apply_missing_field_preference(
                    chunk, current_mapping, migration_rules
                )
                if skipped_in_batch:
                    print(
                        f"📋 [SQL] Missing-field preference (skip): omitted {skipped_in_batch} rows "
                        f"(read {raw_batch_len} from extraction)"
                    )
                if not chunk:
                    start_index += raw_batch_len
                    await self.job_store.update(job_id, {
                        "processed_records": start_index,
                        "successful_records": successful,
                        "failed_records": failed,
                    })
                    continue

                result = await service.migrate_data_to_database(
                    data=chunk,
                    mapping=current_mapping,
                    connector_id=connector_id,
                    table_name=table_name,
                    user_id=user_id,
                    operation=operation,
                    batch_id=batch_id,
                    document_type=document_type,
                    start_index=start_index,
                    destination_name=destination_name,
                    migration_job_id=job_id,
                    job_store=self.job_store,
                )
                if not batch_id and result.get("batch_id"):
                    batch_id = result["batch_id"]
                    await self.job_store.update(job_id, {"batch_id": batch_id})

                processed = result.get("records_migrated", len(chunk))
                chunk_failed = len(chunk) - processed
                successful += processed
                failed += chunk_failed
                start_index += raw_batch_len
                await self.job_store.update(job_id, {
                    "processed_records": start_index,
                    "successful_records": successful,
                    "failed_records": failed,
                })

                job_data = await self.job_store.get(job_id, {})
                current_processed = job_data.get("processed_records", start_index)
                for milestone, target in milestones.items():
                    if (
                        current_processed >= target
                        and milestone not in self.milestone_notifications.get(job_id, [])
                    ):
                        self.milestone_notifications.setdefault(job_id, []).append(milestone)
                        await MigrationRecordsService.update_batch_status(
                            batch_id=batch_id,
                            status="in_progress",
                            successful_count=successful,
                            failed_count=failed,
                        )

            await self.job_store.update(job_id, {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
            })

            # Update final batch status to completed
            if batch_id:
                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="completed",
                    successful_count=successful,
                    failed_count=failed
                )

            # Deduct credits for migration (skip if unlimited plan)
            if successful > 0 and batch_id:
                try:
                    from services.entitlements import get_entitlements
                    from utils.credit_config import compute_migration_credits
                    user_plan = await get_entitlements().get_user_plan(user_id)
                    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                    if credits_per_month != -1:
                        credits = compute_migration_credits(successful)
                        if credits > 0:
                            await get_entitlements().deduct(
                                user_id=user_id,
                                amount=credits,
                                reference_type="migration",
                                reference_id=batch_id,
                            )
                            print(f"✅ [DB] Credits deducted: {credits} for batch {batch_id}")
                except Exception as e:
                    print(f"⚠️ [DB] Failed to deduct migration credits: {e}")

            from services.migration.completed_event_metadata import log_migration_completed_chat_event

            await log_migration_completed_chat_event(job_id)
        except Exception as exc:
            from utils.migration_cancel import MigrationCancelledError
            if isinstance(exc, MigrationCancelledError):
                print(f"🛑 SQL migration job {job_id} cancelled by user (during chunk processing)")
                await self.job_store.update(job_id, {
                    "status": "cancelled",
                    "completed_at": datetime.utcnow().isoformat(),
                    "processed_records": start_index,
                    "successful_records": successful,
                    "failed_records": failed,
                })
                if batch_id:
                    await MigrationRecordsService.update_batch_status(
                        batch_id=batch_id,
                        status="cancelled",
                        successful_count=successful,
                        failed_count=failed,
                        error_summary="Cancelled by user",
                    )
                return
            await self.job_store.update(job_id, {
                "status": "failed",
                "error_summary": str(exc),
            })
            await MigrationRecordsService.update_batch_status(
                batch_id=batch_id,
                status="failed",
                successful_count=successful,
                failed_count=total_records - successful,
                error_summary=str(exc),
            )

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = await self.job_store.get(job_id)
        return job if job else {"status": "unknown", "job_id": job_id}

    async def get_all_jobs(self, user_id: str) -> List[Dict[str, Any]]:
        all_jobs = await self.job_store.get_all_jobs() if hasattr(self.job_store, "get_all_jobs") else {}
        if not isinstance(all_jobs, dict):
            all_jobs = {}
        return [
            {"job_id": jid, **jdata}
            for jid, jdata in all_jobs.items()
            if jdata.get("user_id") == user_id and jdata.get("destination_type") == "sql"
        ]

    def get_job_milestones(self, job_id: str) -> List[int]:
        return self.milestone_notifications.get(job_id, [])

    def get_pending_milestone_notifications(self, job_id: str) -> List[int]:
        notifications = self.milestone_notifications.get(job_id, [])
        self.milestone_notifications[job_id] = []
        return notifications


database_background_migration_service = DatabaseBackgroundMigrationService()

