"""Background workers that run reusable-flow migrations for SQL, file, Google Sheets, and Salesforce."""

import asyncio
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from db.connection import database
from db.extracted_schema import get_flow_details
from services.background_migration_service import background_migration_service
from services.chat_service import ChatService
from services.database_background_migration_service import database_background_migration_service
from services.extraction_results_service import ExtractionResultsService
from services.flow_executions_service import FlowExecutionsService
from services.gsheets_background_migration_service import gsheets_background_migration_service
from services.incremental_sync_service import IncrementalSyncService
from services.migration_records_service import MigrationRecordsService
from services.sql_background_processing_service import get_task_status
from utils.batch_config import compute_migration_batch_size
from utils.job_stores import extraction_jobs, migration_jobs
from utils.salesforceapimigration import get_valid_salesforce_token

# Helper function to get field mappings from flow_id
async def get_saved_field_mappings(flow_id: str, user_id: str):
    """
    Get field mappings from a saved flow
    """
    field_mappings = await database.fetch_all(
        """
        SELECT 
            id, extracted_field_name, salesforce_object, salesforce_field, lookup_of, is_duplicate_check, is_required, created_at
        FROM field_mappings
        WHERE migration_flow_id = :flow_id
        ORDER BY created_at
        """,
        {"flow_id": flow_id}
    )
    return field_mappings


def _resolve_extracted_field_to_actual_key(
    extracted_field_name: str, actual_data_keys: Optional[List[str]]
) -> str:
    """
    When extraction row keys differ only by case or post-processing label (e.g. Email vs email),
    use the key from the first extracted row so migrate_data_with_bulk_api record.get(source_field) hits.
    """
    if not extracted_field_name or not actual_data_keys:
        return extracted_field_name or ""
    if extracted_field_name in actual_data_keys:
        return extracted_field_name
    low = extracted_field_name.lower()
    for k in actual_data_keys:
        if k.lower() == low:
            return k
    return extracted_field_name


# Helper function to transform database mappings to migration format
def transform_mappings_for_migration(field_mappings, actual_data_keys=None, destination_type: Optional[str] = "salesforce"):
    """
    Transform database field mappings to the format expected by migrate_data_to_salesforce
    Converts from database format to migration dictionary format
    Reconstructs array structures for split fields (multiple mappings with same extracted_field_name)
    """
    # First, group mappings by extracted_field_name to detect split fields
    grouped_mappings = {}
    for mapping in field_mappings:
        # Convert Record object to dictionary if needed
        if hasattr(mapping, '_mapping'):
            mapping = dict(mapping)
        
        raw_name = mapping.get("extracted_field_name")
        extracted_field_name = _resolve_extracted_field_to_actual_key(raw_name, actual_data_keys)

        if extracted_field_name not in grouped_mappings:
            grouped_mappings[extracted_field_name] = []
        
        grouped_mappings[extracted_field_name].append(mapping)
    
    transformed_mapping = {}
    dest_type = (destination_type or "salesforce").lower()
    
    # Process each group of mappings
    for extracted_field_name, mappings_list in grouped_mappings.items():
        # With the root cause fix, field names should match exactly
        # Keep the actual_data_keys parameter for backward compatibility and safety
        if actual_data_keys and extracted_field_name not in actual_data_keys:
            print(f"⚠️ Warning: Field '{extracted_field_name}' not found in actual data keys: {list(actual_data_keys)}")
            # Still include it in case it's needed for required fields
            # The migration function will handle missing fields gracefully
        
        # Sort mappings by created_at to maintain order for split fields
        mappings_list.sort(key=lambda x: x.get("created_at") or 0)
        
        # If multiple mappings share the same extracted_field_name, reconstruct as array (split field)
        if len(mappings_list) > 1:
            # This is a split field - reconstruct as array
            array_mapping = []
            for idx, mapping in enumerate(mappings_list):
                object_value = mapping.get("salesforce_object")
                field_value = mapping.get("salesforce_field")
                lookup_value = mapping.get("lookup_of")
                is_duplicate = mapping.get("is_duplicate_check", False)
                is_required = mapping.get("is_required", False)
                
                if dest_type == "google_sheets":
                    array_item = {
                        "sheet": object_value,
                        "column": field_value,
                        "is_duplicate_check": is_duplicate,
                        "is_required": is_required,
                    }
                elif dest_type == "sql":
                    array_item = {
                        "table": object_value,
                        "column": field_value,
                        "is_duplicate_check": is_duplicate,
                        "is_required": is_required,
                        **({"lookup_of": lookup_value} if lookup_value else {}),
                    }
                else:
                    array_item = {
                        "object": object_value,
                        "field": field_value,
                        "is_duplicate_check": is_duplicate,
                        "is_required": is_required,
                        **({"lookup_of": lookup_value} if lookup_value else {}),
                    }
                
                array_mapping.append(array_item)
            
            transformed_mapping[extracted_field_name] = array_mapping
            print(f"   ↳ {extracted_field_name} -> [{len(array_mapping)} split fields]")
        else:
            # Single mapping (simple one-to-one)
            mapping = mappings_list[0]
            object_value = mapping.get("salesforce_object")
            field_value = mapping.get("salesforce_field")
            lookup_value = mapping.get("lookup_of")
            is_duplicate = mapping.get("is_duplicate_check", False)
            is_required = mapping.get("is_required", False)

            if dest_type == "google_sheets":
                transformed_mapping[extracted_field_name] = {
                    "sheet": object_value,
                    "column": field_value,
                    "is_duplicate_check": is_duplicate,
                    "is_required": is_required,
                }
            elif dest_type == "sql":
                transformed_mapping[extracted_field_name] = {
                    "table": object_value,
                    "column": field_value,
                    "is_duplicate_check": is_duplicate,
                    "is_required": is_required,
                    **({"lookup_of": lookup_value} if lookup_value else {}),
                }
            else:
                transformed_mapping[extracted_field_name] = {
                    "object": object_value,
                    "field": field_value,
                    "is_duplicate_check": is_duplicate,
                    "is_required": is_required,
                    **({"lookup_of": lookup_value} if lookup_value else {}),
                }
            
            print(f"   ↳ {extracted_field_name} -> {transformed_mapping[extracted_field_name]}")
    
    return transformed_mapping


async def _get_first_record_keys_for_mapping(job_id: str, user_id: str) -> Optional[List[str]]:
    """
    Get keys from the first extraction record without loading full data.
    Used for transform_mappings_for_migration when using source_job_id batch migration.
    """
    async for chunk in ExtractionResultsService.iterate_extraction_records(
        job_id=job_id, user_id=user_id, batch_size=1
    ):
        if chunk and len(chunk) > 0:
            return list(chunk[0].keys())
        break
    return None


async def get_background_job(job_id: str):
    """Fetch background job details from any supported migration service. All migration jobs (Salesforce, Google Sheets, SQL) use Redis (migration_jobs)."""
    # Single lookup - all migration types use the same Redis store
    job = await migration_jobs.get(job_id)
    if not job or job.get("status") == "unknown":
        return None, None
    # Derive job_type from job data or job_id prefix
    job_type = job.get("destination_type")
    if not job_type and job_id.startswith("gsheets_"):
        job_type = "google_sheets"
    elif not job_type and job_id.startswith("sql_"):
        job_type = "sql"
    elif not job_type:
        job_type = "salesforce"
    return job, job_type


            # Don't fail if saving fails
async def process_sql_flow_migration_background(
    task_id: str,
    flow_id: str,
    user_id: str,
    flow_execution_id: Optional[str] = None,
):
    """
    Background job processor for SQL flow migration
    Waits for SQL extraction to complete, then starts migration using saved flow mapping
    Uses the same approach as file upload flows
    """
    print(f"🔄 [SQL FLOW MIGRATION BG] Starting background migration - task_id: {task_id}, flow_id: {flow_id}, user_id: {user_id}")

    async def _fail_fe(reason: str):
        if flow_execution_id:
            try:
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", (reason or "")[:2000]
                )
            except Exception:
                pass

    try:
        # Wait for SQL extraction to complete
        max_wait_time = 300  # 5 minutes
        wait_interval = 2  # 2 seconds
        elapsed_time = 0
        print(f"🔄 [SQL FLOW MIGRATION BG] Waiting for SQL extraction to complete - task_id: {task_id}")
        
        while elapsed_time < max_wait_time:
            # Check if SQL task is completed
            from services.sql_background_processing_service import get_task_status
            task_status = await get_task_status(task_id)
            
            if not task_status:
                print(f"❌ SQL task {task_id} not found")
                await _fail_fe("SQL task not found")
                return
            
            if task_status.get('status') == 'completed':
                # SQL extraction completed
                print(f"✅ [SQL FLOW MIGRATION BG] SQL extraction completed - task_id: {task_id}")
                break
            elif task_status.get('status') == 'failed':
                print(f"❌ [SQL FLOW MIGRATION BG] SQL extraction failed for task {task_id}: {task_status.get('error')}")
                await _fail_fe(task_status.get("error") or "SQL extraction failed")
                return
            else:
                print(f"🔄 [SQL FLOW MIGRATION BG] SQL extraction still in progress - task_id: {task_id}, status: {task_status.get('status')}, elapsed: {elapsed_time}s")
            
            # Wait before checking again
            await asyncio.sleep(wait_interval)
            elapsed_time += wait_interval
        
        if elapsed_time >= max_wait_time:
            print(f"⏰ Timeout waiting for SQL extraction task {task_id}")
            await _fail_fe("Timeout waiting for SQL extraction")
            return
        
        # Get the SQL extraction result
        result = task_status.get('result')
        print(f"🔄 [SQL FLOW MIGRATION BG] Extracted result - task_id: {task_id}, has_result: {bool(result)}")
        if not result:
            print(f"❌ [SQL FLOW MIGRATION BG] No result found for SQL task {task_id}")
            await _fail_fe("No extraction result for SQL task")
            return
        
        # Get total_records from extraction metadata - no full data load (task_id = job_id in extraction_results)
        total_records = await ExtractionResultsService.get_extraction_record_count(task_id, user_id)
        try:
            fd_inc = await get_flow_details(flow_id, UUID(user_id))
            sch_inc = fd_inc.get("schema") or {}
            await IncrementalSyncService.persist_after_extraction_success(
                flow_id,
                user_id,
                task_id,
                (sch_inc.get("extracted_schema_type") or "sql").lower(),
                sch_inc.get("sql_query"),
            )
        except Exception as e:
            print(f"⚠️ [INCREMENTAL] SQL persist watermark failed: {e}")

        if total_records is None or total_records == 0:
            print(
                f"ℹ️ [SQL FLOW MIGRATION BG] Extraction returned no rows; skipping migration for task {task_id}"
            )
            if flow_execution_id:
                try:
                    await FlowExecutionsService.complete_execution(
                        flow_execution_id,
                        "succeeded",
                        None,
                        sync_outcome="no_changes",
                    )
                except Exception as e:
                    print(f"⚠️ flow_executions complete (0 records): {e}")
            return

        # Get first record keys for mapping (minimal load - batch_size=1)
        actual_data_keys = await _get_first_record_keys_for_mapping(task_id, user_id)

        print(f"🔄 [SQL FLOW MIGRATION BG] Fetching field mappings - flow_id: {flow_id}")
        field_mappings = await get_saved_field_mappings(flow_id, user_id)
        print(f"🔄 [SQL FLOW MIGRATION BG] Field mappings retrieved - count: {len(field_mappings) if field_mappings else 0}")
        if not field_mappings:
            print(f"❌ [SQL FLOW MIGRATION BG] No field mappings found for flow {flow_id}")
            raise Exception("No field mappings found for this flow")

        user_uuid = UUID(user_id)
        print(f"🔄 [SQL FLOW MIGRATION BG] Fetching flow details - flow_id: {flow_id}")
        flow_details = await get_flow_details(flow_id, user_uuid)
        flow_info = flow_details.get("flow") if flow_details else {}
        schema_data = flow_details.get("schema") if flow_details else {}

        dest_type = task_status.get("destination", "salesforce")
        transformed_mapping = transform_mappings_for_migration(field_mappings, actual_data_keys, dest_type)

        migration_rules = flow_info.get("migration_rules") or {}
        from utils.migration_rules_utils import get_mapping_transformation

        transformation_rules = get_mapping_transformation(migration_rules)

        # Cleanup and transformation are free; no credit deduction

        dest_id = flow_info.get("org_id")
        dest_type, dest_name = await _resolve_destination_context(
            user_uuid,
            flow_info.get("destination_type"),
            dest_id,
        )

        if not dest_name:
            extraction_job_data = await extraction_jobs.get(task_id, {})
            dest_name = extraction_job_data.get("destination_name") if extraction_job_data else None

        document_type = result.get("document_type", "Unknown")
        migration_job_id = None
        batch_id = None

        print(f"🔄 [SQL FLOW MIGRATION BG] Starting migration - dest_type: {dest_type}, dest_id: {dest_id}, dest_name: {dest_name}")
        if dest_type == "salesforce":
            if not dest_id:
                print(f"❌ [SQL FLOW MIGRATION BG] Salesforce destination org is missing - flow_id: {flow_id}")
                raise Exception("Salesforce destination org is missing for this flow")

            extraction_job_data = await extraction_jobs.get(task_id, {}) or {}
            from utils.salesforceapimigration import get_valid_salesforce_token

            salesforce_creds = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=dest_id,
                connected_app_id=extraction_job_data.get("connected_app_id"),
            )
            replay_guard_pair_id = (
                str(flow_info.get("parent_flow_id")).strip()
                if flow_info.get("parent_flow_id")
                else str(flow_id).strip()
            )
            replay_guard_direction = "B_TO_A" if flow_info.get("parent_flow_id") else "A_TO_B"
            print(
                f"🛡️ [REPLAY_GUARD][MAIN] flow_id={flow_id} parent_flow_id={flow_info.get('parent_flow_id')} "
                f"pair_id={replay_guard_pair_id} direction={replay_guard_direction} dest_type={dest_type}"
            )

            migration_job_id = await background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                access_token=salesforce_creds["access_token"],
                instance_url=salesforce_creds["instance_url"],
                batch_id=None,
                batch_size=compute_migration_batch_size(total_records, "salesforce"),
                org_id=salesforce_creds.get("org_id") or dest_id,
                connected_app_id=salesforce_creds.get("connected_app_id") or extraction_job_data.get("connected_app_id"),
                destination_name=dest_name,
                source_access_token=None,
                source_instance_url=None,
                source_job_id=task_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
                replay_guard_flow_pair_id=replay_guard_pair_id,
                replay_guard_direction=replay_guard_direction,
                replay_guard_source_flow_id=str(flow_id),
            )
            print(
                f"🛡️ [REPLAY_GUARD][MAIN] queued migration_job_id={migration_job_id} "
                f"pair_id={replay_guard_pair_id} direction={replay_guard_direction}"
            )
            job_info, _ = await get_background_job(migration_job_id)
            batch_id = job_info.get("batch_id") if job_info else None

        elif dest_type == "google_sheets":
            spreadsheet_id = dest_id or schema_data.get("connector_id")
            sheet_name = schema_data.get("table_name")
            sheet_name_from_mapping = next(
                (
                    value.get("sheet")
                    for value in transformed_mapping.values()
                    if isinstance(value, dict) and value.get("sheet")
                ),
                None,
            ) or next(
                (
                    mapping_dict.get("salesforce_object")
                    for m in field_mappings
                    for mapping_dict in [dict(m) if hasattr(m, "_mapping") else m]
                ),
                None,
            )
            if sheet_name_from_mapping:
                sheet_name = sheet_name_from_mapping

            batch_name = f"SQL_Flow_GSheets_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "spreadsheet_name": dest_name,
                "operation": "upsert",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="google_sheets",
            )
            migration_job_id = await gsheets_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                spreadsheet_name=dest_name,
                operation="upsert",
                batch_id=batch_id,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=task_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        elif dest_type == "sql":
            connector_id = dest_id
            table_name = schema_data.get("table_name")
            table_name_from_mapping = next(
                (
                    mapping_dict.get("salesforce_object")
                    for m in field_mappings
                    for mapping_dict in [dict(m) if hasattr(m, "_mapping") else m]
                ),
                None,
            )
            if table_name_from_mapping:
                table_name = table_name_from_mapping

            if not connector_id or not table_name:
                raise Exception("SQL destination details missing for this flow")

            batch_name = f"SQL_Flow_DB_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "connector_id": connector_id,
                "table_name": table_name,
                "destination_name": dest_name or table_name,
                "operation": "append",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="sql",
            )
            migration_job_id = await database_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                connector_id=connector_id,
                table_name=table_name,
                document_type=document_type,
                operation="append",
                batch_id=batch_id,
                destination_name=dest_name or table_name,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=task_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        else:
            raise Exception(f"Unsupported destination type '{dest_type}' for SQL flow reuse")

        if flow_execution_id and batch_id:
            await FlowExecutionsService.link_migration_batch(flow_execution_id, batch_id)

        # Poll for migration completion, then send email (credits deducted in database_background_migration_service)
        poll_interval = 3
        poll_max_time = 3600
        poll_elapsed = 0
        successful_count = 0
        failed_count = 0
        job_info = None

        while poll_elapsed < poll_max_time:
            await asyncio.sleep(poll_interval)
            poll_elapsed += poll_interval

            job_info, _ = await get_background_job(migration_job_id)

            if not job_info or job_info.get("status") == "unknown":
                continue

            status = job_info.get("status")
            successful_count = job_info.get("successful_records", 0)
            failed_count = job_info.get("failed_records", 0)

            if status in ("completed", "failed", "cancelled"):
                break

        try:
            from utils.email_utils import send_migration_complete_email

            user_result = await database.fetch_one(
                "SELECT email FROM users WHERE id = :user_id",
                {"user_id": user_id},
            )
            if user_result and user_result["email"]:
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await send_migration_complete_email(
                    to_email=user_result["email"],
                    flow_id=flow_id,
                    document_type=document_type,
                    record_count=total_records,
                    successful_count=successful_count,
                    skipped_count=0,
                    failed_count=failed_count,
                    sync_outcome=sync_outcome,
                    database=database
                )
        except Exception as email_error:
            print(f"⚠️ Email notification failed: {email_error}")

        if flow_execution_id:
            st = (job_info or {}).get("status") if job_info else None
            if st == "completed":
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "succeeded", sync_outcome=sync_outcome
                )
            elif st in ("failed", "cancelled"):
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", f"Migration status: {st}", sync_outcome="failed"
                )

    except Exception as migration_error:
        print(f"❌ [SQL FLOW MIGRATION BG] SQL flow migration error: {migration_error}")
        import traceback
        traceback.print_exc()
        try:
            await _fail_fe(str(migration_error))
        except Exception:
            pass


async def process_file_flow_migration_background(
    job_id: str,
    flow_id: str,
    user_id: str,
    file_key: str,
    conversation_id: Optional[str],
    extraction_goals: str,
    document_type: str,
    document_description: Optional[str],
    extraction_rules: Optional[Dict[str, Any]],
    mapping_context_summary: Optional[str],
):
    """
    Background job processor for file flow migration.
    Uses source_job_id + iterate_extraction_records - no full data load.
    Same pattern as SQL/GSheets/Salesforce flow migrations.
    """
    try:
        print(f"🔄 [FILE FLOW MIGRATION BG] Starting - job_id={job_id}, flow_id={flow_id}")

        job_data = await extraction_jobs.get(job_id, {})
        if not job_data:
            print(f"❌ [FILE FLOW MIGRATION BG] Job not found - job_id={job_id}")
            return

        result = job_data.get("result")
        if not result:
            print(f"❌ [FILE FLOW MIGRATION BG] No result in job - job_id={job_id}")
            return

        total_records = await ExtractionResultsService.get_extraction_record_count(job_id, user_id)
        if total_records is None or total_records == 0:
            print(f"❌ [FILE FLOW MIGRATION BG] No data to migrate - job_id={job_id}")
            return

        actual_data_keys = await _get_first_record_keys_for_mapping(job_id, user_id)
        field_mappings = await get_saved_field_mappings(flow_id, user_id)
        if not field_mappings:
            raise Exception("No field mappings found for this flow")

        dest_type = (job_data.get("destination") or "salesforce").lower()
        transformed_mapping = transform_mappings_for_migration(
            field_mappings, actual_data_keys, dest_type
        )

        user_uuid = UUID(user_id)
        flow_details = await get_flow_details(flow_id, user_uuid)
        flow_info = flow_details.get("flow") if flow_details else {}
        schema_data = flow_details.get("schema") if flow_details else {}
        dest_id = flow_info.get("org_id")
        dest_type, dest_name = await _resolve_destination_context(
            user_uuid, flow_info.get("destination_type"), dest_id
        )
        if not dest_name:
            dest_name = job_data.get("destination_name")

        migration_rules = flow_info.get("migration_rules") or {}
        from utils.migration_rules_utils import get_mapping_transformation

        transformation_rules = get_mapping_transformation(migration_rules)

        # Transformation is free; no credit deduction

        migration_job_id = None
        batch_id = None

        if dest_type == "salesforce":
            if not dest_id:
                raise Exception("Salesforce destination org is missing for this flow")
            salesforce_creds = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=dest_id,
                connected_app_id=job_data.get("connected_app_id"),
            )
            migration_job_id = await background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                access_token=salesforce_creds["access_token"],
                instance_url=salesforce_creds["instance_url"],
                batch_id=None,
                batch_size=compute_migration_batch_size(total_records, "salesforce"),
                org_id=salesforce_creds.get("org_id") or dest_id,
                connected_app_id=salesforce_creds.get("connected_app_id") or job_data.get("connected_app_id"),
                destination_name=dest_name,
                source_access_token=None,
                source_instance_url=None,
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )
            ji = await background_migration_service.get_job_status(migration_job_id)
            batch_id = ji.get("batch_id") if ji else None

        elif dest_type == "google_sheets":
            spreadsheet_id = dest_id or schema_data.get("connector_id")
            sheet_name = schema_data.get("table_name")
            sheet_name_from_mapping = next(
                (m.get("salesforce_object") or m.get("sheet")
                for m in (dict(x) if hasattr(x, "_mapping") else x for x in field_mappings)
                if isinstance(m, dict) and (m.get("salesforce_object") or m.get("sheet"))
            ), None) or next(
                (v.get("sheet") for v in transformed_mapping.values()
                if isinstance(v, dict) and v.get("sheet")),
                None,
            )
            if sheet_name_from_mapping:
                sheet_name = sheet_name_from_mapping
            batch_name = f"File_Flow_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "spreadsheet_name": dest_name,
                "operation": "upsert",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="google_sheets",
            )
            migration_job_id = await gsheets_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                spreadsheet_name=dest_name,
                operation="upsert",
                batch_id=batch_id,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        elif dest_type == "sql":
            connector_id = dest_id or schema_data.get("connector_id")
            table_name = schema_data.get("table_name") or next(
                (m.get("salesforce_object") for m in (dict(x) if hasattr(x, "_mapping") else x for x in field_mappings)
                if isinstance(m, dict) and m.get("salesforce_object")),
                None,
            )
            if not connector_id or not table_name:
                raise Exception("SQL destination details missing for this flow")
            batch_name = f"File_Flow_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "connector_id": connector_id,
                "table_name": table_name,
                "destination_name": dest_name or table_name,
                "operation": "append",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="sql",
            )
            migration_job_id = await database_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                connector_id=connector_id,
                table_name=table_name,
                document_type=document_type,
                operation="append",
                batch_id=batch_id,
                destination_name=dest_name or table_name,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        else:
            raise Exception(f"Unsupported destination type '{dest_type}' for file flow")

        # Poll for completion, then email (migration credits deducted in destination workers)
        poll_interval = 3
        poll_max_time = 3600
        poll_elapsed = 0
        successful_count = 0
        failed_count = 0

        while poll_elapsed < poll_max_time:
            await asyncio.sleep(poll_interval)
            poll_elapsed += poll_interval
            job_info, _ = await get_background_job(migration_job_id)
            if not job_info or job_info.get("status") == "unknown":
                continue
            status = job_info.get("status")
            successful_count = job_info.get("successful_records", 0)
            failed_count = job_info.get("failed_records", 0)
            if status in ("completed", "failed", "cancelled"):
                break

        try:
            from utils.email_utils import send_migration_complete_email
            user_result = await database.fetch_one(
                "SELECT email FROM users WHERE id = :user_id",
                {"user_id": user_id},
            )
            if user_result and user_result["email"]:
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await send_migration_complete_email(
                    to_email=user_result["email"],
                    flow_id=flow_id,
                    document_type=document_type,
                    record_count=total_records,
                    successful_count=successful_count,
                    skipped_count=0,
                    failed_count=failed_count,
                    sync_outcome=sync_outcome,
                    database=database,
                )
        except Exception as email_error:
            print(f"⚠️ [FILE FLOW MIGRATION BG] Email failed: {email_error}")

        migration_results = {
            "summary": {"successful": successful_count, "failed": failed_count},
            "records_migrated": successful_count,
        }
        await extraction_jobs.update(job_id, {
            "status": "completed",
            "progress": 100,
            "result": {
                "status": "migration_completed",
                "message": "Reusable flow migration completed successfully",
                "migration_results": migration_results,
                "flow_id": flow_id,
                "document_type": document_type,
                "record_count": total_records,
                "successful_count": successful_count,
                "destination": dest_type,
                "destination_name": dest_name,
                "source_type": job_data.get("source_type"),
                "classification": job_data.get("classification"),
                "intent": job_data.get("intent"),
            },
        })

        data_quality_report = None
        try:
            from utils.data_quality_analyzer import analyze_data_quality
            async for batch in ExtractionResultsService.iterate_extraction_records(
                job_id=job_id, user_id=user_id, batch_size=min(1000, total_records)
            ):
                if batch:
                    sample = batch[:500]
                    data_quality_report = await asyncio.to_thread(analyze_data_quality, sample)
                    break
        except Exception as qe:
            print(f"⚠️ [FILE FLOW MIGRATION BG] Data quality analysis skipped: {qe}")

        if conversation_id:
            try:
                from handlers.classification_handler import create_intent_state
                source_name = file_key.split("_", 1)[1] if "_" in file_key else file_key
                intent_state = create_intent_state(
                    active_intent=job_data.get("classification", {}).get("intent", "migrate"),
                    active_source="file",
                    active_destination=dest_type,
                    source_name=source_name,
                    destination_name=dest_name,
                    job_id=job_id,
                    state="extraction_completed",
                )
                msg = await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type="extraction_completed",
                    event_data={
                        "job_id": job_id,
                        "source_type": "file",
                        "source_name": source_name,
                        "record_count": total_records,
                        "status": "success",
                        "destination": dest_type,
                        "destination_name": dest_name,
                        "file_key": file_key,
                        "document_type": document_type,
                        "document_description": document_description,
                        "extraction_rules": extraction_rules,
                        "mapping_context_summary": mapping_context_summary,
                        "intent_state": intent_state,
                        "data_quality_report": data_quality_report,
                    },
                )
                if msg and msg.get("id"):
                    r = (await extraction_jobs.get(job_id, {})).get("result", {})
                    r["extraction_message_id"] = msg["id"]
                    r["intent_state"] = intent_state
                    r["mapping_context_summary"] = mapping_context_summary
                    r["data_quality_report"] = data_quality_report
                    await extraction_jobs.update(job_id, {"result": r})
            except Exception as e:
                print(f"⚠️ [FILE FLOW MIGRATION BG] Log data event failed: {e}")

        print(f"✅ [FILE FLOW MIGRATION BG] Completed - job_id={job_id}, successful={successful_count}, failed={failed_count}")

    except Exception as e:
        print(f"❌ [FILE FLOW MIGRATION BG] Error: {e}")
        import traceback
        traceback.print_exc()
        await extraction_jobs.update(job_id, {
            "status": "failed",
            "progress": 0,
            "error": str(e),
            "result": None,
        })


async def process_google_sheets_flow_migration_background(
    job_id: str,
    flow_id: str,
    user_id: str,
    flow_execution_id: Optional[str] = None,
):
    """
    Background job processor for Google Sheets flow migration.
    Waits for extraction, then delegates to background migration services using source_job_id
    (iterate_extraction_records) - no full data load. Eliminates OOM for large datasets.
    """

    async def _fail_fe(reason: str):
        if flow_execution_id:
            try:
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", (reason or "")[:2000]
                )
            except Exception:
                pass

    try:
        max_wait_time = 300
        wait_interval = 2
        elapsed_time = 0

        while elapsed_time < max_wait_time:
            if not await extraction_jobs.exists(job_id):
                print(f"❌ [GSHEETS FLOW MIGRATION BG] Job not found - job_id: {job_id}")
                return

            job_status = await extraction_jobs.get(job_id)

            if job_status.get("status") == "completed":
                break
            if job_status.get("status") == "failed":
                print(f"❌ [GSHEETS FLOW MIGRATION BG] Google Sheets extraction failed for job {job_id}: {job_status.get('error')}")
                return

            await asyncio.sleep(wait_interval)
            elapsed_time += wait_interval

        if elapsed_time >= max_wait_time:
            print(f"⏰ [GSHEETS FLOW MIGRATION BG] Timeout waiting for Google Sheets extraction job {job_id}")
            return

        job_data = await extraction_jobs.get(job_id, {})
        result = job_data.get("result")
        if not result:
            print(f"❌ [GSHEETS FLOW MIGRATION BG] No extraction result found for Google Sheets job {job_id}")
            return

        # Get total_records from extraction metadata - no full data load
        total_records = await ExtractionResultsService.get_extraction_record_count(job_id, user_id)
        if total_records is None or total_records == 0:
            print(
                f"ℹ️ [GSHEETS FLOW MIGRATION BG] Extraction returned no rows; skipping migration for job {job_id}"
            )
            if flow_execution_id:
                try:
                    await FlowExecutionsService.complete_execution(flow_execution_id, "succeeded", None)
                except Exception as e:
                    print(f"⚠️ flow_executions complete (0 records): {e}")
            return

        # Get first record keys for mapping (minimal load - batch_size=1)
        actual_data_keys = await _get_first_record_keys_for_mapping(job_id, user_id)

        field_mappings = await get_saved_field_mappings(flow_id, user_id)
        if not field_mappings:
            print(f"❌ [GSHEETS FLOW MIGRATION BG] No field mappings found for flow {flow_id}")
            raise Exception("No field mappings found for this flow")

        dest_type = job_data.get("destination")
        transformed_mapping = transform_mappings_for_migration(field_mappings, actual_data_keys, dest_type)

        user_uuid = UUID(user_id)
        flow_details = await get_flow_details(flow_id, user_uuid)
        flow_info = flow_details.get("flow") if flow_details else {}
        schema_data = flow_details.get("schema") if flow_details else {}
        
        # Note: extraction_rules are already applied during extraction (via saved_extraction_rules)
        # when flow_id is present, so we don't need to apply them again here.
        # This ensures rules are applied once during extraction (consistent with file flow).
        migration_rules = flow_info.get("migration_rules") or {}
        from utils.migration_rules_utils import get_mapping_transformation

        transformation_rules = get_mapping_transformation(migration_rules)

        # Transformation is free; no credit deduction

        dest_id = flow_info.get("org_id")
        dest_type, dest_name = await _resolve_destination_context(
            user_uuid,
            flow_info.get("destination_type"),
            dest_id,
        )

        document_type = result.get("document_type", "Unknown")
        migration_job_id = None
        batch_id = None

        if dest_type == "salesforce":
            from utils.salesforceapimigration import get_valid_salesforce_token

            salesforce_creds = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=dest_id,
                connected_app_id=job_data.get("connected_app_id"),
            )
            migration_job_id = await background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                access_token=salesforce_creds["access_token"],
                instance_url=salesforce_creds["instance_url"],
                batch_id=None,
                batch_size=compute_migration_batch_size(total_records, "salesforce"),
                org_id=salesforce_creds.get("org_id") or dest_id,
                connected_app_id=salesforce_creds.get("connected_app_id") or job_data.get("connected_app_id"),
                destination_name=dest_name,
                source_access_token=None,
                source_instance_url=None,
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )
            job_info, _ = await get_background_job(migration_job_id)
            batch_id = job_info.get("batch_id") if job_info else None

        elif dest_type == "google_sheets":
            spreadsheet_id = dest_id or schema_data.get("connector_id")
            sheet_name = schema_data.get("table_name")
            sheet_name_from_mapping = None
            for mapping_entry in field_mappings:
                mapping_dict = dict(mapping_entry) if hasattr(mapping_entry, "_mapping") else mapping_entry
                candidate = mapping_dict.get("salesforce_object")
                if candidate:
                    sheet_name_from_mapping = candidate
                    break
            if not sheet_name_from_mapping:
                for value in transformed_mapping.values():
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and item.get("sheet"):
                                sheet_name_from_mapping = item.get("sheet")
                                break
                        if sheet_name_from_mapping:
                            break
                    elif isinstance(value, dict) and value.get("sheet"):
                        sheet_name_from_mapping = value.get("sheet")
                        break
            if sheet_name_from_mapping:
                sheet_name = sheet_name_from_mapping

            batch_name = f"GSheets_Flow_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name,
                "spreadsheet_name": dest_name,
                "operation": "upsert",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="google_sheets",
            )
            migration_job_id = await gsheets_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                spreadsheet_name=dest_name,
                operation="upsert",
                batch_id=batch_id,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        elif dest_type == "sql":
            connector_id = dest_id or schema_data.get("connector_id")
            table_name = schema_data.get("table_name")
            if not connector_id or not table_name:
                for mapping_entry in field_mappings:
                    mapping_dict = dict(mapping_entry) if hasattr(mapping_entry, "_mapping") else mapping_entry
                    candidate = mapping_dict.get("salesforce_object")
                    if candidate:
                        table_name = candidate
                        break
            if not connector_id or not table_name:
                print(f"❌ [GSHEETS FLOW MIGRATION BG] SQL destination details missing - connector_id: {connector_id}, table_name: {table_name}")
                raise Exception("SQL destination details missing for this flow")

            batch_name = f"SQL_Flow_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {
                "document_type": document_type,
                "connector_id": connector_id,
                "table_name": table_name,
                "destination_name": dest_name or table_name,
                "operation": "append",
            }
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=batch_name,
                document_type=document_type,
                total_records=total_records,
                mapping_config=mapping_config,
                destination_type="sql",
            )
            migration_job_id = await database_background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                connector_id=connector_id,
                table_name=table_name,
                document_type=document_type,
                operation="append",
                batch_id=batch_id,
                destination_name=dest_name or table_name,
                batch_size=compute_migration_batch_size(total_records, dest_type),
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
            )

        else:
            raise Exception(f"Unsupported destination type '{dest_type}' for Google Sheets flow reuse")

        if flow_execution_id and batch_id:
            await FlowExecutionsService.link_migration_batch(flow_execution_id, batch_id)

        # Poll for migration completion, then send email (credits/usage in gsheets_migration_service per chunk)
        poll_interval = 3
        poll_max_time = 3600  # 1 hour max
        poll_elapsed = 0
        successful_count = 0
        failed_count = 0
        job_info = None

        while poll_elapsed < poll_max_time:
            await asyncio.sleep(poll_interval)
            poll_elapsed += poll_interval

            job_info, _ = await get_background_job(migration_job_id)

            if not job_info or job_info.get("status") == "unknown":
                continue

            status = job_info.get("status")
            successful_count = job_info.get("successful_records", 0)
            failed_count = job_info.get("failed_records", 0)

            if status in ("completed", "failed", "cancelled"):
                break

        try:
            from utils.email_utils import send_migration_complete_email

            user_result = await database.fetch_one(
                "SELECT email FROM users WHERE id = :user_id",
                {"user_id": user_id},
            )
            if user_result and user_result["email"]:
                await send_migration_complete_email(
                    to_email=user_result["email"],
                    flow_id=flow_id,
                    document_type=document_type,
                    record_count=total_records,
                    successful_count=successful_count,
                    skipped_count=0,
                    failed_count=failed_count,
                    database=database
                )
        except Exception as email_error:
            print(f"⚠️ Email notification failed: {email_error}")

        if flow_execution_id:
            st = (job_info or {}).get("status") if job_info else None
            if st == "completed":
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "succeeded", sync_outcome=sync_outcome
                )
            elif st in ("failed", "cancelled"):
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", f"Migration status: {st}", sync_outcome="failed"
                )

    except Exception as migration_error:
        print(f"❌ [GSHEETS FLOW MIGRATION BG] Google Sheets flow migration error: {migration_error}")
        import traceback
        traceback.print_exc()
        try:
            await _fail_fe(str(migration_error))
        except Exception:
            pass


async def _resolve_destination_context(
    user_id: UUID,
    destination_type: Optional[str],
    destination_id: Optional[str],
) -> Tuple[str, Optional[str]]:
    dest_type = (destination_type or "salesforce").lower()
    dest_name: Optional[str] = None

    try:
        if dest_type == "salesforce" and destination_id:
            # Token-centric: org_display_name is on salesforce_tokens, no need for salesforce_connected_apps
            dest_row = await database.fetch_one(
                """
                SELECT COALESCE(st.org_display_name, st.instance_url) AS display_name
                FROM salesforce_tokens st
                WHERE st.user_id = :user_id AND st.org_id = :org_id
                ORDER BY st.issued_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "org_id": destination_id},
            )
            if dest_row:
                dest_row = dict(dest_row)
                if dest_row.get("display_name"):
                    dest_name = dest_row["display_name"]
        elif dest_type == "google_sheets" and destination_id:
            dest_row = await database.fetch_one(
                """
                SELECT spreadsheet_name
                FROM user_google_spreadsheets
                WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
                ORDER BY last_accessed_at DESC
                LIMIT 1
                """,
                {"user_id": str(user_id), "spreadsheet_id": destination_id},
            )
            if dest_row:
                dest_row = dict(dest_row)
                if dest_row.get("spreadsheet_name"):
                    dest_name = dest_row["spreadsheet_name"]
        elif dest_type == "sql" and destination_id:
            dest_row = await database.fetch_one(
                """
                SELECT name
                FROM sql_connectors
                WHERE user_id = :user_id AND id = :connector_id
                """,
                {"user_id": str(user_id), "connector_id": destination_id},
            )
            if dest_row:
                dest_row = dict(dest_row)
                if dest_row.get("name"):
                    dest_name = dest_row["name"]
    except Exception as e:
        print(f"⚠️ Failed to resolve destination context ({dest_type}, {destination_id}): {e}")

    return dest_type, dest_name


async def process_salesforce_flow_migration_background(
    job_id: str,
    flow_id: str,
    user_id: str,
    flow_execution_id: Optional[str] = None,
):
    """Wait for a Salesforce extraction job to finish, then migrate records to the saved destination."""
    print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Starting background migration - job_id: {job_id}, flow_id: {flow_id}, user_id: {user_id}")

    async def _fail_fe(reason: str):
        if flow_execution_id:
            try:
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", (reason or "")[:2000]
                )
            except Exception:
                pass

    try:
        max_wait_time = 300
        wait_interval = 2
        elapsed_time = 0
        print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Waiting for Salesforce extraction to complete - job_id: {job_id}")

        while elapsed_time < max_wait_time:
            if not await extraction_jobs.exists(job_id):
                print(f"❌ [SALESFORCE FLOW MIGRATION BG] Job not found - job_id: {job_id}")
                return

            job_status = await extraction_jobs.get(job_id)

            if job_status.get("status") == "completed":
                print(f"✅ [SALESFORCE FLOW MIGRATION BG] Salesforce extraction completed - job_id: {job_id}")
                break
            if job_status.get("status") == "failed":
                print(f"❌ [SALESFORCE FLOW MIGRATION BG] Salesforce extraction failed for job {job_id}: {job_status.get('error')}")
                return
            else:
                print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Salesforce extraction still in progress - job_id: {job_id}, status: {job_status.get('status')}, elapsed: {elapsed_time}s")

            await asyncio.sleep(wait_interval)
            elapsed_time += wait_interval

        if elapsed_time >= max_wait_time:
            print(f"⏰ Timeout waiting for Salesforce extraction job {job_id}")
            return

        job_data = await extraction_jobs.get(job_id, {})
        result = job_data.get("result")
        print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Extracted result - job_id: {job_id}, has_result: {bool(result)}")
        if not result:
            print(f"❌ [SALESFORCE FLOW MIGRATION BG] No extraction result found for Salesforce job {job_id}")
            return

        # Get total_records from extraction metadata - no full data load
        total_records = await ExtractionResultsService.get_extraction_record_count(job_id, user_id)
        try:
            fd_inc = await get_flow_details(flow_id, UUID(user_id))
            sch_inc = fd_inc.get("schema") or {}
            await IncrementalSyncService.persist_after_extraction_success(
                flow_id,
                user_id,
                job_id,
                (sch_inc.get("extracted_schema_type") or "salesforce").lower(),
                sch_inc.get("sql_query"),
            )
        except Exception as e:
            print(f"⚠️ [INCREMENTAL] Salesforce persist watermark failed: {e}")

        if total_records is None or total_records == 0:
            print(
                f"ℹ️ [SALESFORCE FLOW MIGRATION BG] Extraction returned no rows; skipping migration for job {job_id}"
            )
            if flow_execution_id:
                try:
                    await FlowExecutionsService.complete_execution(
                        flow_execution_id,
                        "succeeded",
                        None,
                        sync_outcome="no_changes",
                    )
                except Exception as e:
                    print(f"⚠️ flow_executions complete (0 records): {e}")
            return

        # Get first record keys for mapping (minimal load - batch_size=1)
        actual_data_keys = await _get_first_record_keys_for_mapping(job_id, user_id)

        print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Fetching field mappings - flow_id: {flow_id}")
        field_mappings = await get_saved_field_mappings(flow_id, user_id)
        print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Field mappings retrieved - count: {len(field_mappings) if field_mappings else 0}")
        if not field_mappings:
            print(f"❌ [SALESFORCE FLOW MIGRATION BG] No field mappings found for flow {flow_id}")
            raise Exception("No field mappings found for this flow")

        dest_type = job_data.get("destination")
        transformed_mapping = transform_mappings_for_migration(field_mappings, actual_data_keys, dest_type)

        user_uuid = UUID(user_id)
        flow_details = await get_flow_details(flow_id, user_uuid)
        flow_info = flow_details.get("flow") if flow_details else {}
        schema_data = flow_details.get("schema") if flow_details else {}
        
        # Extract and apply extraction_rules from migration_rules if present
        # These are applied to raw data before mapping transformation rules
        migration_rules = flow_info.get("migration_rules") or {}
        extraction_rules = migration_rules.get("extraction_rules")
        from utils.migration_rules_utils import get_mapping_transformation

        transformation_rules = get_mapping_transformation(migration_rules)

        # Cleanup and transformation are free; no credit deduction

        dest_id = flow_info.get("org_id")
        dest_type, dest_name = await _resolve_destination_context(
            user_uuid,
            flow_info.get("destination_type"),
            dest_id,
        )
        print(f"🔄 [SALESFORCE FLOW MIGRATION BG] Starting migration - dest_type: {dest_type}, dest_id: {dest_id}, dest_name: {dest_name}")

        document_type = result.get("document_type", "Unknown")
        migration_job_id = None
        batch_id = None

        if dest_type == "salesforce":
            if not dest_id:
                print(f"❌ [SALESFORCE FLOW MIGRATION BG] Salesforce destination org is missing - flow_id: {flow_id}")
                raise Exception("Salesforce destination org is missing for this flow")

            from utils.salesforceapimigration import get_valid_salesforce_token

            job_data = await extraction_jobs.get(job_id, {})
            salesforce_creds = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=dest_id,
                connected_app_id=job_data.get("connected_app_id"),
            )

            source_access_token = None
            source_instance_url = None
            if job_data.get("source_type") == "salesforce" and job_data.get("org_id"):
                try:
                    source_token_info = await get_valid_salesforce_token(
                        user_id=user_id,
                        org_id=job_data.get("org_id"),
                        connected_app_id=job_data.get("connected_app_id"),
                    )
                    source_access_token = source_token_info["access_token"]
                    source_instance_url = source_token_info["instance_url"]
                except Exception as e:
                    print(f"⚠️ [SALESFORCE FLOW MIGRATION BG] Could not fetch source token for rich text: {e}")

            replay_guard_pair_id = (
                str(flow_info.get("parent_flow_id")).strip()
                if flow_info.get("parent_flow_id")
                else str(flow_id).strip()
            )
            replay_guard_direction = "B_TO_A" if flow_info.get("parent_flow_id") else "A_TO_B"
            print(
                f"🛡️ [REPLAY_GUARD][MAIN] flow_id={flow_id} parent_flow_id={flow_info.get('parent_flow_id')} "
                f"pair_id={replay_guard_pair_id} direction={replay_guard_direction} dest_type={dest_type}"
            )

            migration_job_id = await background_migration_service.start_background_migration(
                user_id=user_id,
                mapping=transformed_mapping,
                document_type=document_type,
                access_token=salesforce_creds["access_token"],
                instance_url=salesforce_creds["instance_url"],
                batch_id=None,
                batch_size=compute_migration_batch_size(total_records, "salesforce"),
                org_id=salesforce_creds.get("org_id") or dest_id,
                connected_app_id=salesforce_creds.get("connected_app_id") or job_data.get("connected_app_id"),
                destination_name=dest_name,
                source_access_token=source_access_token,
                source_instance_url=source_instance_url,
                source_job_id=job_id,
                total_records=total_records,
                transformation_rules=transformation_rules,
                is_transformed_data=False,
                migration_rules=migration_rules,
                replay_guard_flow_pair_id=replay_guard_pair_id,
                replay_guard_direction=replay_guard_direction,
                replay_guard_source_flow_id=str(flow_id),
            )
            print(
                f"🛡️ [REPLAY_GUARD][MAIN] queued migration_job_id={migration_job_id} "
                f"pair_id={replay_guard_pair_id} direction={replay_guard_direction}"
            )
            job_info, _ = await get_background_job(migration_job_id)
            batch_id = job_info.get("batch_id") if job_info else None

        elif dest_type == "google_sheets":
            spreadsheet_id = dest_id or schema_data.get("connector_id")
            sheet_name = schema_data.get("table_name")
            sheet_name_from_mapping = next(
                (v.get("sheet") for v in transformed_mapping.values() if isinstance(v, dict) and v.get("sheet")),
                None,
            ) or next(
                (c for m in field_mappings for c in [(dict(m) if hasattr(m, "_mapping") else m).get("salesforce_object")] if c),
                None,
            )
            if sheet_name_from_mapping:
                sheet_name = sheet_name_from_mapping

            batch_name = f"SF_Flow_GSheets_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {"document_type": document_type, "spreadsheet_id": spreadsheet_id, "sheet_name": sheet_name, "spreadsheet_name": dest_name, "operation": "upsert"}
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id, batch_name=batch_name, document_type=document_type, total_records=total_records,
                mapping_config=mapping_config, destination_type="google_sheets",
            )
            migration_job_id = await gsheets_background_migration_service.start_background_migration(
                user_id=user_id, mapping=transformed_mapping, document_type=document_type,
                spreadsheet_id=spreadsheet_id, sheet_name=sheet_name, spreadsheet_name=dest_name,
                operation="upsert", batch_id=batch_id, batch_size=compute_migration_batch_size(total_records, dest_type), source_job_id=job_id,
                total_records=total_records, transformation_rules=transformation_rules, is_transformed_data=False,
                migration_rules=migration_rules,
            )

        elif dest_type == "sql":
            connector_id = dest_id or schema_data.get("connector_id")
            table_name = schema_data.get("table_name")
            table_name_from_mapping = next(
                (dict(m).get("salesforce_object") if hasattr(m, "_mapping") else m.get("salesforce_object") for m in field_mappings),
                None,
            )
            if table_name_from_mapping:
                table_name = table_name_from_mapping

            if not connector_id or not table_name:
                raise Exception("SQL destination details missing for this flow")

            batch_name = f"SF_Flow_SQL_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            mapping_config = {"document_type": document_type, "connector_id": connector_id, "table_name": table_name, "destination_name": dest_name or table_name, "operation": "append"}
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id, batch_name=batch_name, document_type=document_type, total_records=total_records,
                mapping_config=mapping_config, destination_type="sql",
            )
            migration_job_id = await database_background_migration_service.start_background_migration(
                user_id=user_id, mapping=transformed_mapping, connector_id=connector_id, table_name=table_name,
                document_type=document_type, operation="append", batch_id=batch_id, destination_name=dest_name or table_name,
                batch_size=compute_migration_batch_size(total_records, dest_type), source_job_id=job_id, total_records=total_records,
                transformation_rules=transformation_rules, is_transformed_data=False,
                migration_rules=migration_rules,
            )

        else:
            raise Exception(f"Unsupported destination type '{dest_type}' for Salesforce flow reuse")

        if flow_execution_id and batch_id:
            await FlowExecutionsService.link_migration_batch(flow_execution_id, batch_id)

        # Poll for migration completion, then send email (credits/usage handled in migration workers)
        poll_interval = 3
        poll_max_time = 3600
        poll_elapsed = 0
        successful_count = 0
        failed_count = 0
        job_info = None

        while poll_elapsed < poll_max_time:
            await asyncio.sleep(poll_interval)
            poll_elapsed += poll_interval

            job_info, _ = await get_background_job(migration_job_id)

            if not job_info or job_info.get("status") == "unknown":
                continue

            status = job_info.get("status")
            successful_count = job_info.get("successful_records", 0)
            failed_count = job_info.get("failed_records", 0)

            if status in ("completed", "failed", "cancelled"):
                break
        
        try:
            from utils.email_utils import send_migration_complete_email

            user_result = await database.fetch_one(
                "SELECT email FROM users WHERE id = :user_id",
                {"user_id": user_id},
            )
            if user_result and user_result["email"]:
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await send_migration_complete_email(
                    to_email=user_result["email"],
                    flow_id=flow_id,
                    document_type=document_type,
                    record_count=total_records,
                    successful_count=successful_count,
                    skipped_count=0,
                    failed_count=failed_count,
                    sync_outcome=sync_outcome,
                    database=database
                )
        except Exception as email_error:
            print(f"⚠️ Email notification failed: {email_error}")

        if flow_execution_id:
            st = (job_info or {}).get("status") if job_info else None
            if st == "completed":
                sync_outcome = (
                    "no_changes"
                    if int(successful_count or 0) == 0 and int(failed_count or 0) == 0
                    else "completed"
                )
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "succeeded", sync_outcome=sync_outcome
                )
            elif st in ("failed", "cancelled"):
                await FlowExecutionsService.complete_execution(
                    flow_execution_id, "failed", f"Migration status: {st}", sync_outcome="failed"
                )

    except Exception as migration_error:
        print(f"❌ [SALESFORCE FLOW MIGRATION BG] Salesforce flow migration error: {migration_error}")
        import traceback
        traceback.print_exc()
        try:
            await _fail_fe(str(migration_error))
        except Exception:
            pass
