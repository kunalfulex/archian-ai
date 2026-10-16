import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from services.migration_records_service import MigrationRecordsService
from services.salesforce_migration_flow import migrate_single_record_to_salesforce
from services.chat_service import ChatService
from utils.redis_service import migration_job_store
from utils.async_job_store import AsyncJobStore
from utils.missing_field_preference import apply_missing_field_preference
from services.replay_guard_service import ReplayGuardService


class BackgroundMigrationService:
    """Service for handling background migration jobs to process large datasets"""
    
    def __init__(self):
        # Use Redis-backed job store with in-memory fallback
        self.job_store = AsyncJobStore(migration_job_store)
    
    async def start_background_migration(
        self,
        user_id: str,
        mapping: Dict[str, Any],
        document_type: str,
        access_token: str,
        instance_url: str,
        batch_id: str = None,
        batch_size: int = 1000,
        max_concurrent: int = 5,
        org_id: Optional[str] = None,
        connected_app_id: Optional[int] = None,
        destination_name: Optional[str] = None,
        salesforce_token_id: Optional[str] = None,
        source_access_token: Optional[str] = None,
        source_instance_url: Optional[str] = None,
        source_job_id: Optional[str] = None,
        total_records: Optional[int] = None,
        transformation_rules: Optional[Dict[str, Any]] = None,
        is_transformed_data: bool = False,
        conversation_id: Optional[str] = None,
        migration_rules: Optional[Dict[str, Any]] = None,
        replay_guard_flow_pair_id: Optional[str] = None,
        replay_guard_direction: Optional[str] = None,
        replay_guard_source_flow_id: Optional[str] = None,
    ) -> str:
        """
        Start a background migration job. Data is loaded from extraction_result_records
        by source_job_id - no data array is passed.

        Args:
            user_id: User ID for tracking
            mapping: Field mapping configuration
            document_type: Type of document being migrated
            access_token: Salesforce access token
            instance_url: Salesforce instance URL
            batch_id: Optional batch ID (created if not provided)
            batch_size: Number of records per batch
            source_job_id: Extraction job_id - backend loads records from DB
            total_records: Record count (from extraction_results)
            transformation_rules: Optional - mapping-stage rules; applied per batch when rows are not pre-transformed
            is_transformed_data: If True, rows are already mapping-transformed (skip batch transform)
            migration_rules: Optional missing_field_preference (placeholder vs skip)

        Returns:
            Job ID for tracking the background process
        """
        if not source_job_id:
            raise ValueError("source_job_id is required for migration")
        if total_records is None or total_records < 0:
            raise ValueError("total_records is required when using source_job_id")

        job_id = str(uuid.uuid4())

        # Use provided batch_id or create new one if not provided
        if not batch_id:
            batch_name = f"Background_Migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {}
            if isinstance(mapping, dict):
                mapping_config = dict(mapping)
            else:
                mapping_config["mapping"] = mapping
            if destination_name:
                mapping_config["destination_name"] = destination_name
            if org_id:
                mapping_config["org_id"] = org_id
            if connected_app_id is not None:
                mapping_config["connected_app_id"] = connected_app_id

            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config or mapping,
                destination_type="salesforce",
            )
        else:
            print(f"📊 Using provided batch_id for background migration: {batch_id}")

        # Store job information (Redis-backed)
        await self.job_store.set(job_id, {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "running",
            "total_records": total_records,
            "processed_records": 0,
            "successful_records": 0,
            "failed_records": 0,
            "started_at": datetime.utcnow().isoformat(),
            "error_summary": None,
            "org_id": org_id,
            "connected_app_id": connected_app_id,
            "destination_name": destination_name,
            "destination": "salesforce",
            "salesforce_token_id": salesforce_token_id,
            "destination_connector_id": str(salesforce_token_id) if salesforce_token_id else None,
            "source_job_id": source_job_id,
            "instance_url": instance_url,
            "conversation_id": conversation_id,
            "replay_guard_flow_pair_id": replay_guard_flow_pair_id,
            "replay_guard_direction": replay_guard_direction,
            "replay_guard_source_flow_id": replay_guard_source_flow_id,
        })
        print(
            f"🛡️ [REPLAY_GUARD][START] job_id={job_id} source_job_id={source_job_id} "
            f"pair_id={replay_guard_flow_pair_id} direction={replay_guard_direction} "
            f"source_flow_id={replay_guard_source_flow_id}"
        )

        # Start background task
        asyncio.create_task(
            self._process_migration_job(
                job_id=job_id,
                batch_id=batch_id,
                user_id=user_id,
                mapping=mapping,
                access_token=access_token,
                instance_url=instance_url,
                batch_size=batch_size,
                max_concurrent=max_concurrent,
                org_id=org_id,
                connected_app_id=connected_app_id,
                destination_name=destination_name,
                source_access_token=source_access_token,
                source_instance_url=source_instance_url,
                source_job_id=source_job_id,
                transformation_rules=transformation_rules,
                is_transformed_data=is_transformed_data,
                migration_rules=migration_rules,
                replay_guard_flow_pair_id=replay_guard_flow_pair_id,
                replay_guard_direction=replay_guard_direction,
                replay_guard_source_flow_id=replay_guard_source_flow_id,
            )
        )

        return job_id
    
    async def _process_migration_job(
        self,
        job_id: str,
        batch_id: str,
        user_id: str,
        mapping: Dict[str, Any],
        access_token: str,
        instance_url: str,
        batch_size: int,
        max_concurrent: int,
        org_id: Optional[str] = None,
        connected_app_id: Optional[int] = None,
        destination_name: Optional[str] = None,
        source_access_token: Optional[str] = None,
        source_instance_url: Optional[str] = None,
        source_job_id: Optional[str] = None,
        transformation_rules: Optional[Dict[str, Any]] = None,
        is_transformed_data: bool = False,
        migration_rules: Optional[Dict[str, Any]] = None,
        replay_guard_flow_pair_id: Optional[str] = None,
        replay_guard_direction: Optional[str] = None,
        replay_guard_source_flow_id: Optional[str] = None,
    ):
        """Process the migration job in the background, loading records from DB by source_job_id."""
        try:
            from services.extraction_results_service import ExtractionResultsService

            if source_job_id:
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
                    print(f"❌ Background migration {job_id}: {err}")
                    await self.job_store.update(job_id, {
                        "status": "failed",
                        "error_summary": err,
                        "completed_at": datetime.utcnow().isoformat(),
                    })
                    await MigrationRecordsService.update_batch_status(
                        batch_id=batch_id,
                        status="failed",
                        error_summary=err,
                    )
                    await self._log_migration_completed_event(job_id)
                    return

            job_data = await self.job_store.get(job_id, {}) or {}
            total_records = job_data.get("total_records", 0)
            successful_count = 0
            failed_count = 0
            processed_so_far = 0
            total_rich_text_images_uploaded = 0
            total_attachments_uploaded = 0

            milestones = {
                25: int(total_records * 0.25),
                50: int(total_records * 0.50),
                75: int(total_records * 0.75),
                100: total_records,
            }
            achieved_milestones = set()

            print(f"🎯 Background migration job {job_id} started with {total_records} records (source_job_id={source_job_id})")
            print(f"🎯 Destination context -> org_id: {org_id}, connected_app_id: {connected_app_id}, destination_name: {destination_name}")
            print(
                f"🛡️ [REPLAY_GUARD][PROCESS] job_id={job_id} "
                f"pair_id={replay_guard_flow_pair_id!r} direction={replay_guard_direction!r} "
                f"source_flow_id={replay_guard_source_flow_id!r}"
            )

            current_mapping = mapping

            async for batch_data in ExtractionResultsService.iterate_extraction_records(
                job_id=source_job_id,
                user_id=user_id,
                batch_size=batch_size,
                record_ordinals=(migration_rules or {}).get("record_ordinals"),
            ):
                # Check for user-initiated cancellation
                job_data = await self.job_store.get(job_id, {}) or {}
                if job_data.get("cancelled"):
                    print(f"🛑 Migration job {job_id} cancelled by user")
                    await self.job_store.update(job_id, {
                        "status": "cancelled",
                        "completed_at": datetime.utcnow().isoformat(),
                        "processed_records": processed_so_far,
                        "successful_records": successful_count,
                        "failed_records": failed_count,
                    })
                    await self._log_migration_completed_event(job_id)
                    return

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
                            data=batch_data,
                            mapping=current_mapping,
                            transformation_rules=transformation_rules,
                            user_id=user_id,
                            org_id=org_id,
                            destination="salesforce",
                        )
                        batch_data = transformed_data
                        current_mapping = transformed_mapping
                    except Exception as transform_err:
                        print(f"⚠️ Batch transformation error: {transform_err}")
                        failed_count += len(batch_data)
                        processed_so_far += len(batch_data)
                        await self.job_store.update(job_id, {
                            "processed_records": processed_so_far,
                            "successful_records": successful_count,
                            "failed_records": failed_count,
                        })
                        continue

                raw_batch_len = len(batch_data)

                batch_data, skipped_in_batch = apply_missing_field_preference(
                    batch_data, current_mapping, migration_rules
                )
                if skipped_in_batch:
                    print(
                        f"📋 Missing-field preference (skip): omitted {skipped_in_batch} rows "
                        f"in batch (read {raw_batch_len} from extraction)"
                    )

                if not batch_data:
                    processed_so_far += raw_batch_len
                    await self.job_store.update(job_id, {
                        "processed_records": processed_so_far,
                        "successful_records": successful_count,
                        "failed_records": failed_count,
                    })
                    print(
                        f"📊 Job {job_id}: batch fully skipped by missing-field rules "
                        f"({raw_batch_len} source rows), progress {processed_so_far}/{total_records}"
                    )
                    continue

                # Replay guard (bidirectional loop suppression): drop rows recently written by opposite direction.
                kept_meta: List[Dict[str, str]] = []
                if replay_guard_flow_pair_id and replay_guard_direction:
                    try:
                        pre_guard_count = len(batch_data)
                        batch_data, kept_meta, skipped_by_guard = await ReplayGuardService.filter_bouncebacks(
                            records=batch_data,
                            mapping=current_mapping,
                            flow_pair_id=replay_guard_flow_pair_id,
                            direction=replay_guard_direction,
                        )
                        print(
                            f"🛡️ Replay guard checked={pre_guard_count} kept={len(batch_data)} "
                            f"skipped={skipped_by_guard} pair={replay_guard_flow_pair_id} dir={replay_guard_direction}"
                        )
                    except Exception as rg_err:
                        print(f"⚠️ Replay guard filter error (continuing without suppression): {rg_err}")
                        kept_meta = []
                else:
                    print(
                        f"🛡️ [REPLAY_GUARD][DISABLED] job_id={job_id} "
                        f"pair_id={replay_guard_flow_pair_id!r} direction={replay_guard_direction!r}"
                    )
                if not batch_data:
                    processed_so_far += raw_batch_len
                    await self.job_store.update(job_id, {
                        "processed_records": processed_so_far,
                        "successful_records": successful_count,
                        "failed_records": failed_count,
                    })
                    continue

                batch_num = (processed_so_far // batch_size) + 1
                print(f"🔄 Processing batch {batch_num}: {len(batch_data)} records")

                # Use hybrid routing (Bulk API 2.0 or REST API based on batch size)
                try:
                    from services.salesforce_migration_flow import migrate_data_to_salesforce
                    from utils.mapping_utils import transform_mapping_for_migration

                    flat_mapping = transform_mapping_for_migration(
                        current_mapping,
                        use_destination_field_keys=bool(is_transformed_data),
                    )

                    batch_result = await migrate_data_to_salesforce(
                        data=batch_data,
                        mapping=flat_mapping,
                        document_type="Background Migration",
                        access_token=access_token,
                        instance_url=instance_url,
                        user_id=user_id,
                        batch_id=batch_id,
                        org_id=org_id,
                        connected_app_id=connected_app_id,
                        destination_name=destination_name,
                        use_bulk_api=None,
                        bulk_api_threshold=50,
                        source_access_token=source_access_token,
                        source_instance_url=source_instance_url,
                        migration_job_id=job_id,
                        job_store=self.job_store,
                    )

                    # Count results from the batch
                    if isinstance(batch_result, dict):
                        summary = batch_result.get("summary", {})
                        batch_successful = summary.get("successful", 0)
                        batch_failed = summary.get("failed", 0)
                        successful_count += batch_successful
                        failed_count += batch_failed
                        total_rich_text_images_uploaded += summary.get("rich_text_images_uploaded", 0)
                        total_attachments_uploaded += summary.get("attachments_uploaded", 0)
                        # Replay guard marking (REST path only for accurate per-row success mapping).
                        if (
                            replay_guard_flow_pair_id
                            and replay_guard_direction
                            and isinstance(batch_result.get("results"), list)
                            and kept_meta
                        ):
                            successful_meta: List[Dict[str, str]] = []
                            results_list = batch_result.get("results") or []
                            for idx2, rec_result in enumerate(results_list):
                                if idx2 >= len(kept_meta):
                                    break
                                is_success = True
                                if isinstance(rec_result, dict) and rec_result.get("status") == "error":
                                    is_success = False
                                elif isinstance(rec_result, dict):
                                    for obj_res in rec_result.values():
                                        if isinstance(obj_res, dict) and obj_res.get("status") == "error":
                                            is_success = False
                                            break
                                if is_success:
                                    meta = kept_meta[idx2]
                                    if meta.get("record_key") and meta.get("payload_hash"):
                                        successful_meta.append(meta)
                            if successful_meta:
                                await ReplayGuardService.mark_successful_writes(
                                    flow_pair_id=replay_guard_flow_pair_id,
                                    direction=replay_guard_direction,
                                    successful_meta=successful_meta,
                                    source_flow_id=replay_guard_source_flow_id,
                                )
                                print(
                                    f"🛡️ Replay guard marked {len(successful_meta)} successful row(s) "
                                    f"for pair {replay_guard_flow_pair_id} dir={replay_guard_direction}"
                                )
                        elif (
                            replay_guard_flow_pair_id
                            and replay_guard_direction
                            and kept_meta
                            and (batch_result.get("method") == "bulk_api")
                        ):
                            successful_indices = batch_result.get("successful_record_indices") or []
                            successful_meta = [
                                kept_meta[i]
                                for i in successful_indices
                                if isinstance(i, int) and 0 <= i < len(kept_meta)
                            ]
                            if successful_meta:
                                await ReplayGuardService.mark_successful_writes(
                                    flow_pair_id=replay_guard_flow_pair_id,
                                    direction=replay_guard_direction,
                                    successful_meta=successful_meta,
                                    source_flow_id=replay_guard_source_flow_id,
                                )
                                print(
                                    f"🛡️ Replay guard marked {len(successful_meta)} successful bulk row(s) "
                                    f"for pair {replay_guard_flow_pair_id} dir={replay_guard_direction}"
                                )
                    else:
                        _attachment_success_statuses = ("linked", "uploaded_not_linked", "linked_existing")
                        for result in batch_result if isinstance(batch_result, list) else []:
                            if isinstance(result, dict) and result.get("status") != "error":
                                successful_count += 1
                                for obj_data in result.values():
                                    if isinstance(obj_data, dict):
                                        total_rich_text_images_uploaded += len(obj_data.get("rich_text_images", []))
                                        for att in obj_data.get("attachments", []):
                                            if isinstance(att, dict) and att.get("status") in _attachment_success_statuses:
                                                total_attachments_uploaded += 1
                            else:
                                failed_count += 1

                except Exception as e:
                    from utils.migration_cancel import MigrationCancelledError
                    if isinstance(e, MigrationCancelledError):
                        print(f"🛑 Migration job {job_id} cancelled by user (bulk batch in progress)")
                        await self.job_store.update(job_id, {
                            "status": "cancelled",
                            "completed_at": datetime.utcnow().isoformat(),
                            "processed_records": processed_so_far,
                            "successful_records": successful_count,
                            "failed_records": failed_count,
                        })
                        if batch_id:
                            await MigrationRecordsService.update_batch_status(
                                batch_id=batch_id,
                                status="cancelled",
                                successful_count=successful_count,
                                failed_count=failed_count,
                                error_summary="Cancelled by user",
                            )
                        await self._log_migration_completed_event(job_id)
                        return
                    print(f"❌ Error processing batch: {e}")
                    failed_count += raw_batch_len

                processed_so_far += raw_batch_len

                # Update job progress (Redis-backed)
                await self.job_store.update(job_id, {
                    "processed_records": processed_so_far,
                    "successful_records": successful_count,
                    "failed_records": failed_count,
                })

                print(f"📊 Job {job_id} progress updated: {processed_so_far}/{total_records} records processed")

                # Check for milestone achievements
                current_processed = processed_so_far
                for milestone_percentage, milestone_target in milestones.items():
                    if current_processed >= milestone_target and milestone_percentage not in achieved_milestones:
                        achieved_milestones.add(milestone_percentage)
                        print(f"🎉 Milestone {milestone_percentage}% achieved! Processed {current_processed}/{total_records} records")
                        
                        # Update batch status (milestone information tracked in job status)
                        await MigrationRecordsService.update_batch_status(
                            batch_id=batch_id,
                            status="in_progress",
                            successful_count=successful_count,
                            failed_count=failed_count
                        )
                
                # Update batch status
                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="in_progress",
                    successful_count=successful_count,
                    failed_count=failed_count
                )
            
            # Mark job as completed (Redis-backed)
            await self.job_store.update(job_id, {
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat(),
                "achieved_milestones": list(achieved_milestones)
            })

            job_row = await self.job_store.get(job_id) or {}

            # Terminal batch status must be set before migration_completed (recovery reads batch row).
            await MigrationRecordsService.update_batch_status(
                batch_id=batch_id,
                status="completed",
                successful_count=successful_count,
                failed_count=failed_count,
                conversation_id=job_row.get("conversation_id"),
                destination_name=job_row.get("destination_name"),
            )

            await self._log_migration_completed_event(job_id)
            
            # Track successful migration in usage_tracking table
            try:
                from services.entitlements import get_entitlements
                await get_entitlements().track_successful_migration(user_id, successful_count)
                print(f"✅ Usage tracking updated: {successful_count} records for user {user_id}")
            except Exception as e:
                print(f"⚠️ Warning: Failed to update usage tracking: {e}")

            # Deduct credits for migration (skip if unlimited plan)
            try:
                from services.entitlements import get_entitlements
                from utils.credit_config import compute_migration_credits
                user_plan = await get_entitlements().get_user_plan(user_id)
                credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                if credits_per_month != -1 and successful_count > 0:
                    credits = compute_migration_credits(successful_count)
                    if credits > 0:
                        await get_entitlements().deduct(
                            user_id=user_id,
                            amount=credits,
                            reference_type="migration",
                            reference_id=batch_id,
                        )
                        print(f"✅ Credits deducted: {credits} for migration batch {batch_id}")
            except Exception as e:
                print(f"⚠️ Warning: Failed to deduct migration credits: {e}")

            # Deduct credits for rich text area images (5 per image; non-blocking)
            if user_id and total_rich_text_images_uploaded > 0:
                try:
                    from services.entitlements import get_entitlements
                    from utils.credit_config import compute_rich_text_image_credits
                    user_plan = await get_entitlements().get_user_plan(user_id)
                    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                    if credits_per_month is not None and credits_per_month != -1:
                        rich_text_credits = compute_rich_text_image_credits(total_rich_text_images_uploaded)
                        if rich_text_credits > 0:
                            await get_entitlements().deduct(
                                user_id=user_id,
                                amount=rich_text_credits,
                                reference_type="rich_text_image",
                                reference_id=batch_id,
                            )
                            print(f"✅ Credits deducted: {rich_text_credits} for {total_rich_text_images_uploaded} rich text images (batch {batch_id})")
                except Exception as e:
                    print(f"⚠️ Warning: Failed to deduct rich text image credits: {e}")

            # Deduct credits for file attachments (5 per file; non-blocking)
            if user_id and total_attachments_uploaded > 0:
                try:
                    from services.entitlements import get_entitlements
                    from utils.credit_config import compute_migration_credits
                    user_plan = await get_entitlements().get_user_plan(user_id)
                    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                    if credits_per_month is not None and credits_per_month != -1:
                        attachment_credits = compute_migration_credits(0, 0, total_attachments_uploaded)
                        if attachment_credits > 0:
                            await get_entitlements().deduct(
                                user_id=user_id,
                                amount=attachment_credits,
                                reference_type="file_attachment",
                                reference_id=batch_id,
                            )
                            print(f"✅ Credits deducted: {attachment_credits} for {total_attachments_uploaded} file attachments (batch {batch_id})")
                except Exception as e:
                    print(f"⚠️ Warning: Failed to deduct file attachment credits: {e}")

            print(f"✅ Background migration job {job_id} completed. Success: {successful_count}, Failed: {failed_count}")
            print(f"🎯 All milestones achieved: {sorted(achieved_milestones)}")
            
        except Exception as e:
            print(f"❌ Background migration job {job_id} failed: {e}")
            
            # Update job status (Redis-backed)
            await self.job_store.update(job_id, {
                "status": "failed",
                "error_summary": str(e),
                "completed_at": datetime.utcnow().isoformat()
            })

            await self._log_migration_completed_event(job_id)
            
            # Update batch status
            await MigrationRecordsService.update_batch_status(
                batch_id=batch_id,
                status="failed",
                error_summary=str(e)
            )
    
    async def _process_single_record(
        self,
        semaphore: asyncio.Semaphore,
        batch_id: str,
        user_id: str,
        record_index: int,
        record: Dict[str, Any],
        mapping: Dict[str, Any],
        access_token: str,
        instance_url: str,
        org_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Process a single record with semaphore for concurrency control"""
        async with semaphore:
            try:
                # Create migration record
                record_id = await MigrationRecordsService.create_migration_record(
                    batch_id=batch_id,
                    user_id=user_id,
                    record_index=record_index,
                    source_data=record,
                    mapped_data=record
                )
                
                # Migrate record
                result = await migrate_single_record_to_salesforce(
                    record, 
                    mapping, 
                    access_token, 
                    instance_url,
                    user_id=user_id,
                    org_id=org_id
                )
                
                # Determine status
                overall_status = "success"
                error_message = None
                
                for obj_result in result.values():
                    if isinstance(obj_result, dict) and obj_result.get("status") == "error":
                        overall_status = "failed"
                        error_message = obj_result.get("message", "Unknown error")
                        break
                
                # Update migration record
                await MigrationRecordsService.update_migration_record_result(
                    record_id=record_id,
                    salesforce_results=result,
                    status=overall_status,
                    error_message=error_message
                )
                
                return {"status": overall_status, "result": result}
                
            except Exception as e:
                # Update migration record with error
                if 'record_id' in locals():
                    await MigrationRecordsService.update_migration_record_result(
                        record_id=record_id,
                        salesforce_results={"error": str(e)},
                        status="failed",
                        error_message=str(e)
                    )
                
                return {"status": "failed", "error": str(e)}
    
    async def _log_migration_completed_event(self, job_id: str) -> None:
        """If the job has conversation_id, log migration_completed so the chat shows correct status on reload."""
        try:
            job_data = await self.job_store.get(job_id)
            if not job_data:
                return
            conversation_id = job_data.get("conversation_id")
            if not conversation_id:
                return
            user_id = job_data.get("user_id", "")
            status = job_data.get("status", "completed")
            destination_type = job_data.get("destination") or "salesforce"
            destination_name = job_data.get("destination_name")
            from services.migration.completed_event_metadata import (
                log_migration_completed_chat_event,
            )

            await log_migration_completed_chat_event(job_id)
        except Exception as e:
            print(f"⚠️ Failed to log migration_completed event: {e}")

    async def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get the current status of a background migration job"""
        return await self.job_store.get(job_id)
    
    async def get_job_milestones(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get milestone information for a specific job"""
        job = await self.job_store.get(job_id)
        if not job:
            return None
        
        total_records = job.get("total_records", 0)
        processed_records = job.get("processed_records", 0)
        
        if total_records == 0:
            return None
        
        milestones = {
            25: {"target": int(total_records * 0.25), "achieved": processed_records >= int(total_records * 0.25)},
            50: {"target": int(total_records * 0.50), "achieved": processed_records >= int(total_records * 0.50)},
            75: {"target": int(total_records * 0.75), "achieved": processed_records >= int(total_records * 0.75)},
            100: {"target": total_records, "achieved": processed_records >= total_records}
        }
        
        return {
            "total_records": total_records,
            "processed_records": processed_records,
            "progress_percentage": round((processed_records / total_records) * 100, 2),
            "milestones": milestones,
            "achieved_milestones": job.get("achieved_milestones", [])
        }
    
    async def get_pending_milestone_notifications(self, job_id: str) -> List[Dict[str, Any]]:
        """Get pending milestone notifications for a job"""
        milestones = await self.get_job_milestones(job_id)
        if not milestones:
            return []
        
        pending_notifications = []
        for percentage, milestone_info in milestones["milestones"].items():
            if milestone_info["achieved"] and percentage not in milestones["achieved_milestones"]:
                pending_notifications.append({
                    "milestone": percentage,
                    "message": f"Milestone {percentage}% achieved! Processed {milestones['processed_records']}/{milestones['total_records']} records",
                    "timestamp": datetime.utcnow().isoformat()
                })
        
        return pending_notifications
    
    async def get_all_jobs(self, user_id: str = None) -> List[Dict[str, Any]]:
        """Get all active jobs, optionally filtered by user"""
        all_jobs = await self.job_store.job_store.get_all_jobs()
        if user_id:
            return [
                {"job_id": job_id, **job_data}
                for job_id, job_data in all_jobs.items()
                if job_data.get("user_id") == user_id
            ]
        return [
            {"job_id": job_id, **job_data}
            for job_id, job_data in all_jobs.items()
        ]
    
    async def cleanup_completed_jobs(self, max_age_hours: int = 24):
        """Clean up completed jobs older than specified hours"""
        current_time = datetime.utcnow()
        jobs_to_remove = []
        
        all_jobs = await self.job_store.job_store.get_all_jobs()
        for job_id, job_data in all_jobs.items():
            if job_data.get("status") in ["completed", "failed"]:
                if "completed_at" in job_data:
                    try:
                        # Convert string back to datetime for calculation
                        completed_at = datetime.fromisoformat(job_data["completed_at"].replace('Z', '+00:00'))
                        age_hours = (current_time - completed_at).total_seconds() / 3600
                        if age_hours > max_age_hours:
                            jobs_to_remove.append(job_id)
                    except:
                        # If conversion fails, skip this job
                        continue
        
        for job_id in jobs_to_remove:
            await self.job_store.delete(job_id)
        
        if jobs_to_remove:
            print(f"🧹 Cleaned up {len(jobs_to_remove)} completed jobs")


# Global instance
background_migration_service = BackgroundMigrationService()
