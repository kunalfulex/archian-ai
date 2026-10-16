"""File extraction background worker (S3 download + structured extract)."""

import asyncio
import os
import tempfile
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import boto3
import pandas as pd

import services.architect_agent as architect
from db.extracted_schema import get_flow_details
from services.chat_service import ChatService
from services.entitlements import get_entitlements
from services.extraction_results_service import ExtractionResultsService
from services.flow_migration_background import (
    _resolve_destination_context,
    get_saved_field_mappings,
    process_file_flow_migration_background,
    transform_mappings_for_migration,
)
from utils.job_stores import extraction_jobs
from utils.redis_service import REDIS_SLIM_RESULT_THRESHOLD
from utils.task_limiter import create_limited_task

async def process_extraction_background(
    job_id: str,
    file_key: str,
    file_extension: str,
    extraction_goals: str,
    user_id: str,
    flow_id: str = None,
    classification: Optional[Dict[str, Any]] = None,
    source_type: Optional[str] = "file",
    destination: Optional[str] = None,
    destination_name: Optional[str] = None,
    intent: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    extracted_data: Optional[List[Dict[str, Any]]] = None,
):
    """
    Background task to process file extraction with detailed logging
    Handles both first-time extraction and re-extraction
    """
    try:
        # Check for user-initiated cancellation
        current_job_pre = await extraction_jobs.get(job_id, {})
        if current_job_pre.get("cancelled"):
            print(f"🛑 File extraction job {job_id} cancelled by user")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return

        # Update job status to processing (Redis-backed)
        # Use update() to preserve existing fields (like cleanup_extraction_rules from cleanup handler)
        await extraction_jobs.update(job_id, {
            "status": "processing",
            "progress": 0,
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat(),
            "extraction_goals": extraction_goals,
            "flow_id": flow_id,
            "source_type": source_type or "file",
            "destination": destination or "salesforce",
            "destination_name": destination_name,
            "classification": classification or {},
            "intent": intent,
            "parent_job_id": parent_job_id,
        })
        
        # Get current job data to check for cleanup intent
        current_job = await extraction_jobs.get(job_id, {})
        
        # Check if this is cleanup intent with existing extracted data (passed as parameter or in job metadata)
        # Priority: 1. Parameter, 2. Job metadata
        cleanup_extracted_data = extracted_data
        if not cleanup_extracted_data:
            cleanup_extracted_data = current_job.get("extracted_data")
        
        is_cleanup_flow = current_job.get("intent") == "cleanup" and cleanup_extracted_data
        
        # Initialize variables for unified processing
        temp_file_path = None
        saved_extraction_rules = None
        quality_report_rules = None  # For cleanup flow: quality report rules to merge with prompt rules
        
        if is_cleanup_flow:
            # Cleanup flow: Generate/merge rules, convert to CSV, then route through normal extraction pipeline
            # Progress callback function (needed for cleanup flow)
            import asyncio as _asyncio
            async_jobs_ref = extraction_jobs
            job_id_ref = job_id
            
            def update_progress(progress_percent, message=""):
                # Schedule async update (fire-and-forget from sync context)
                _asyncio.create_task(async_jobs_ref.update(job_id_ref, {"progress": progress_percent}))
            
            update_progress(10, "Processing cleanup data...")
            
            flat_data = cleanup_extracted_data
            
            # Get original source_schema from parent job (initial extraction)
            # This is the schema BEFORE any transformations (e.g., ['Name', 'Email', ...])
            # We need this for rule accumulation, not the transformed schema
            original_source_schema = None
            if parent_job_id:
                try:
                    parent_job = await extraction_jobs.get(parent_job_id, {})
                    original_source_schema = parent_job.get("source_schema")
                except Exception as e:
                    print(f"⚠️ [FILE CLEANUP FLOW] Error getting source_schema from parent job: {e}")
            
            # Also track current schema from transformed data (for reference)
            if flat_data and len(flat_data) > 0:
                from utils.rule_accumulation import extract_source_schema_from_data
                current_source_schema = extract_source_schema_from_data(flat_data)
                # Store both original and current schemas in job metadata
                await extraction_jobs.update(job_id, {
                    "current_source_schema": current_source_schema,
                    "source_schema": original_source_schema  # Store original source schema for rule accumulation
                })
            
            # Get existing cleanup rules from job metadata (from quality report)
            # These will be merged with prompt-generated rules after extract_structured_data_from_file
            quality_report_rules = current_job.get("cleanup_extraction_rules", {})
            
            # Get cleanup prompt and destination override flag (needed for metadata update later)
            cleanup_prompt = current_job.get("extraction_goals") or extraction_goals
            destination_was_overridden = current_job.get("destination_was_overridden", False)
            
            update_progress(20, "Converting data to CSV format...")
            
            # Convert data to CSV format (same as normal flow does with files)
            # NOTE: We don't apply cleanup rules here - they will be applied by extract_structured_data_from_file
            # Import pandas locally to ensure it's in scope (even though it's imported at top level)
            import pandas
            
            if not flat_data:
                raise ValueError("No data available for cleanup conversion to CSV")
            
            # Create DataFrame from data
            df = pandas.DataFrame(flat_data)
            
            # Create temporary CSV file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as temp_csv:
                df.to_csv(temp_csv.name, index=False)
                temp_file_path = temp_csv.name
            
            # Don't pass saved_extraction_rules - let extract_structured_data_from_file generate rules from prompt
            # We'll merge quality report rules with prompt-generated rules after extraction
            saved_extraction_rules = None
            
            update_progress(60, "Ready for unified processing pipeline...")
            
        else:
            # Normal extraction flow - download file and extract
            # Progress callback function to update job progress (sync callback for async store)
            def update_progress(progress_percent, message=""):
                # Schedule async update (fire-and-forget from sync context)
                # Progress updates are quick and frequent, so we don't limit them
                asyncio.create_task(extraction_jobs.update(job_id, {"progress": progress_percent}))
            
            update_progress(10, "Downloading file from S3...")
            
            # Download file from S3 (run in thread pool to avoid blocking event loop)
            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as temp_file:
                await asyncio.to_thread(s3_client.download_fileobj, BUCKET_NAME, file_key, temp_file)
                temp_file.flush()
                temp_file_path = temp_file.name
                # Update progress (Redis-backed)
                await extraction_jobs.update(job_id, {"progress": 25})
            
            # Get current job data for destination info
            current_job = await extraction_jobs.get(job_id, {})
            
            # For cleanup intent: Don't pass saved_extraction_rules - let extract_structured_data_from_file generate rules from cleanup prompt
            # Quality report rules will be merged afterward
            if current_job.get("intent") == "cleanup":
                saved_extraction_rules = None  # Let extract_structured_data_from_file generate rules from cleanup prompt
            elif flow_id:
                # Reusable flow: Extract extraction_rules from saved flow to apply during extraction
                # This ensures rules are applied during extraction (consistent with Google Sheets/SQL flows)
                try:
                    from db.extracted_schema import get_flow_details
                    user_uuid = UUID(user_id)
                    flow_details = await get_flow_details(flow_id, user_uuid)
                    flow_info = flow_details.get("flow") if flow_details else {}
                    migration_rules = flow_info.get("migration_rules") or {}
                    extraction_rules_from_flow = migration_rules.get("extraction_rules")
                    if extraction_rules_from_flow:
                        saved_extraction_rules = extraction_rules_from_flow
                        # Include source_schema in extraction_rules for ordered application
                        source_schema = migration_rules.get("source_schema")
                        if source_schema:
                            saved_extraction_rules["source_schema"] = source_schema
                        print(f"🔄 [FILE FLOW RERUN] Found extraction_rules in flow - will apply during extraction")
                        print(f"🔄 [FILE FLOW RERUN] source_schema: {source_schema}")
                    else:
                        saved_extraction_rules = None
                except Exception as flow_error:
                    print(f"⚠️ Error fetching extraction_rules from flow: {flow_error}")
                    saved_extraction_rules = None
            else:
                # Normal extraction - no saved rules
                saved_extraction_rules = None
        
        # Unified processing path - both cleanup and normal flows merge here
        if not temp_file_path:
            # This shouldn't happen, but handle gracefully
            await extraction_jobs.update(job_id, {
                "status": "error",
                "progress": 100,
                "result": {
                    "status": "error",
                    "message": "File processing error: No file path available",
                    "data": [],
                    "file_key": file_key
                }
            })
            return
        
        # Progress callback function (reuse for unified processing)
        def update_progress(progress_percent, message=""):
            asyncio.create_task(extraction_jobs.update(job_id, {"progress": progress_percent}))
        
        update_progress(60 if is_cleanup_flow else 25, "Processing with existing file extraction logic...")

        current_job = await extraction_jobs.get(job_id, {})
        if current_job.get("cancelled"):
            print(f"🛑 File extraction job {job_id} cancelled by user")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return

        # Get current job data for destination info (refresh if needed)
        try:
            flat_data, document_type, _, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, updated_destination_name, extraction_rules, mapping_context_summary = await architect.extract_structured_data_from_file(
                local_file_path=temp_file_path,
                extraction_goals=extraction_goals,
                user_id=user_id,
                progress_callback=update_progress,
                destination=current_job.get("destination") or destination or "salesforce",
                destination_name=current_job.get("destination_name") or destination_name,
                source=source_type,
                saved_extraction_rules=saved_extraction_rules  # Pass saved extraction rules (cleanup rules or flow rerun rules)
            )
            
            # Track source_schema for normal extraction (first time extraction)
            # This is needed for rule accumulation when saving flows
            if not is_cleanup_flow and flat_data and len(flat_data) > 0:
                # For normal extraction, source_schema is the original file columns
                # Extract from file if possible, or use first record keys
                try:
                    # Use pandas module (already imported at top level as pd)
                    # Read file to get original columns (before any transformations)
                    if file_extension in ['.csv', '.xlsx', '.xls']:
                        if file_extension == '.csv':
                            df_original = pd.read_csv(temp_file_path, nrows=0)  # Read only headers
                        else:
                            df_original = pd.read_excel(temp_file_path, nrows=0)
                        source_schema = list(df_original.columns.tolist())
                    else:
                        # For other file types, use first record keys as source schema
                        from utils.rule_accumulation import extract_source_schema_from_data
                        source_schema = extract_source_schema_from_data(flat_data)
                    
                    # Store source_schema in job metadata
                    await extraction_jobs.update(job_id, {"source_schema": source_schema})
                    print(f"📋 [FILE EXTRACTION] Tracked source_schema: {source_schema}")
                except Exception as schema_error:
                    print(f"⚠️ [FILE EXTRACTION] Error tracking source_schema: {schema_error}")
                    # Fallback: use first record keys
                    from utils.rule_accumulation import extract_source_schema_from_data
                    source_schema = extract_source_schema_from_data(flat_data)
                    await extraction_jobs.update(job_id, {"source_schema": source_schema})
            
            # Use extraction_rules_result from extraction (which will be saved rules if provided, or OpenAI rules if not)
            extraction_rules = extraction_rules or saved_extraction_rules
            
            # For cleanup flow: Merge quality report rules with prompt-generated rules
            # This preserves all rules from previous operations and adds new cleanup rules
            if is_cleanup_flow and quality_report_rules:
                # Initialize extraction_rules_result from prompt-generated rules
                extraction_rules_result = extraction_rules if extraction_rules else {}
                if not extraction_rules_result:
                    extraction_rules_result = {}
                
                # Merge quality report rules with prompt-generated rules
                # Prompt rules take precedence for filter_rules, deduplication_rules, transformation_rules
                # Quality report data_cleanup_rules should be merged (not overwritten)
                if quality_report_rules.get("data_cleanup_rules"):
                    existing_data_cleanup = extraction_rules_result.get("data_cleanup_rules", {})
                    if isinstance(existing_data_cleanup, dict) and isinstance(quality_report_rules["data_cleanup_rules"], dict):
                        # Merge both dicts - prompt rules take precedence if there are conflicts
                        extraction_rules_result["data_cleanup_rules"] = {**quality_report_rules["data_cleanup_rules"], **existing_data_cleanup}
                    else:
                        # If not both dicts, use quality report rules as fallback
                        if not existing_data_cleanup:
                            extraction_rules_result["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                
                # Use quality report rules for other rule types only if prompt didn't generate them
                if quality_report_rules.get("filter_rules") and not extraction_rules_result.get("filter_rules"):
                    extraction_rules_result["filter_rules"] = quality_report_rules["filter_rules"]
                if quality_report_rules.get("deduplication_rules") and not extraction_rules_result.get("deduplication_rules"):
                    extraction_rules_result["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                if quality_report_rules.get("transformation_rules") and not extraction_rules_result.get("transformation_rules"):
                    extraction_rules_result["transformation_rules"] = quality_report_rules["transformation_rules"]
                
                # Now accumulate with original_extraction_rules to preserve original transformation rules
                # Get existing extraction rules - prioritize original_extraction_rules from job metadata
                existing_extraction_rules = {}
                source_schema = None
                
                # First, try to get original_extraction_rules from job metadata (from initial extraction)
                job_data = await extraction_jobs.get(job_id, {})
                original_extraction_rules_from_job = job_data.get("original_extraction_rules")
                
                # Defensive check: ensure original_extraction_rules_from_job is a dict (not a list or other type)
                if original_extraction_rules_from_job and isinstance(original_extraction_rules_from_job, dict):
                    existing_extraction_rules = original_extraction_rules_from_job
                elif original_extraction_rules_from_job is not None:
                    # original_extraction_rules exists but is not a dict (could be a list or other type)
                    print(f"⚠️ [FILE CLEANUP FLOW] original_extraction_rules found but is not a dict (type: {type(original_extraction_rules_from_job)}), resetting to empty dict")
                    existing_extraction_rules = {}
                
                # Try to get source_schema from flow (if this is a rerun cleanup)
                if flow_id:
                    try:
                        from db.extracted_schema import get_flow_details
                        user_uuid = UUID(user_id)
                        flow_details = await get_flow_details(flow_id, user_uuid)
                        flow_info = flow_details.get("flow") if flow_details else {}
                        migration_rules = flow_info.get("migration_rules") or {}
                        # Only use flow extraction_rules if we don't have original_extraction_rules
                        if not existing_extraction_rules:
                            existing_extraction_rules = migration_rules.get("extraction_rules", {})
                        source_schema = migration_rules.get("source_schema")
                    except Exception as flow_error:
                        print(f"⚠️ [FILE CLEANUP FLOW] Error fetching existing rules from flow: {flow_error}")
                
                # Try to get source_schema from job metadata (prioritize original source_schema, not current_source_schema)
                # We need the ORIGINAL source schema (before transformations) for rule accumulation
                if not source_schema:
                    source_schema = job_data.get("source_schema")  # Original source schema from parent job
                    if not source_schema:
                        # Fallback: try to get from parent job directly
                        if parent_job_id:
                            try:
                                parent_job = await extraction_jobs.get(parent_job_id, {})
                                source_schema = parent_job.get("source_schema")
                            except Exception as e:
                                print(f"⚠️ [FILE CLEANUP FLOW] Error getting source_schema from parent job: {e}")
                    
                    # If still no source_schema, extract from original_file_records (original data before transformations)
                    if not source_schema and original_file_records:
                        from utils.rule_accumulation import extract_source_schema_from_data
                        source_schema = extract_source_schema_from_data(original_file_records)
                
                # Last resort: extract from current flat_data (but this is transformed data, not ideal)
                if not source_schema and flat_data:
                    from utils.rule_accumulation import extract_source_schema_from_data
                    source_schema = extract_source_schema_from_data(flat_data)
                    print(f"⚠️ [FILE CLEANUP FLOW] WARNING: Using transformed data schema as source_schema: {source_schema}")
                
                # Accumulate merged rules with original transformation rules
                if source_schema and existing_extraction_rules:
                    from utils.rule_accumulation import accumulate_cleanup_rules
                    try:
                        # Combine extraction_rules_result (prompt + quality report) with existing_extraction_rules (original transformation rules)
                        # Use extraction_rules_result as new_cleanup_rules (they already include quality report rules)
                        # Ensure existing_extraction_rules is a dict (will be normalized in accumulate_cleanup_rules)
                        if not isinstance(existing_extraction_rules, dict):
                            existing_extraction_rules = {}
                        extraction_rules = accumulate_cleanup_rules(
                            existing_rules=existing_extraction_rules,
                            new_cleanup_rules=extraction_rules_result,
                            source_schema=source_schema
                        )
                    except Exception as acc_error:
                        print(f"⚠️ [FILE CLEANUP FLOW] Error accumulating rules: {acc_error}")
                        import traceback
                        traceback.print_exc()
                        # Fallback: use extraction_rules_result (already merged)
                        extraction_rules = extraction_rules_result
                else:
                    # Fallback: use extraction_rules_result (already merged prompt + quality report)
                    extraction_rules = extraction_rules_result
                    if not extraction_rules:
                        extraction_rules = {}
                
                # Handle destination override case - update metadata if destination was overridden
                if destination_was_overridden and extraction_rules:
                    update_fields = {}
                    if extraction_rules.get("document_type"):
                        update_fields["document_type"] = extraction_rules["document_type"]
                    if extraction_rules.get("document_description"):
                        update_fields["document_description"] = extraction_rules["document_description"]
                    if extraction_rules.get("object_name"):
                        update_fields["object_name"] = extraction_rules["object_name"]
                    
                    # Update only the specific fields in Redis
                    if update_fields:
                        await extraction_jobs.update(job_id, update_fields)
                
                # Apply merged rules to flat_data (especially data_cleanup_rules from quality report)
                if extraction_rules:
                    from utils.extraction_utils import apply_all_extraction_rules
                    flat_data = apply_all_extraction_rules(flat_data, extraction_rules)
                
                # Get preserved document_type/document_description from job_data if available (to maintain metadata)
                # This matches Salesforce, Google Sheets, and SQL cleanup flow behavior
                preserved_document_type = job_data.get("document_type")
                preserved_document_description = job_data.get("document_description")
                preserved_mapping_context = job_data.get("mapping_context_summary")
                
                # Use preserved metadata if available, otherwise use from extract_structured_data_from_file return
                document_type = preserved_document_type or document_type
                document_description = preserved_document_description or document_description
                mapping_context_summary = preserved_mapping_context or mapping_context_summary
                
                # If not in job_data, try to get from parent job
                if not document_type or not document_description:
                    parent_job_id = job_data.get("parent_job_id")
                    if parent_job_id:
                        parent_job = await extraction_jobs.get(parent_job_id, {})
                        if not document_type:
                            document_type = parent_job.get("result", {}).get("document_type")
                        if not document_description:
                            document_description = parent_job.get("result", {}).get("document_description")
                        if not mapping_context_summary:
                            mapping_context_summary = parent_job.get("result", {}).get("mapping_context_summary")
                
                # Final fallback: get from extraction_results table
                if not document_type or not document_description:
                    # ExtractionResultsService is already imported at the top of the file
                    parent_job_id = job_data.get("parent_job_id")
                    if parent_job_id:
                        extraction_result_from_db = await ExtractionResultsService.get_extraction_result(
                            parent_job_id, user_id
                        )
                        if extraction_result_from_db:
                            if not document_type:
                                document_type = extraction_result_from_db.get("document_type")
                            if not document_description:
                                document_description = extraction_result_from_db.get("document_description")
            
            # Update top-level destination_name if it was determined from classification
            if updated_destination_name:
                # Check current destination_name and update if needed
                current_job = await extraction_jobs.get(job_id, {})
                if current_job.get("destination_name") is None or current_job.get("destination_name") == "unknown":
                    await extraction_jobs.update(job_id, {"destination_name": updated_destination_name})
        except Exception as extraction_error:
            print(f"❌ File extraction error: {str(extraction_error)}")
            
            # Clean up temp file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except:
                    pass
            
            # Update job with error status (Redis-backed)
            await extraction_jobs.update(job_id, {
                "status": "error",
                "progress": 100,
                "result": {
                    "status": "error",
                    "message": f"File extraction failed: {str(extraction_error)}",
                    "data": [],
                    "file_key": file_key
                }
            })
            return
        
        # Clean up temporary file (both cleanup and normal flows create temp files)
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except:
                pass
        
        # Update progress to 95% (almost done) (Redis-backed)
        await extraction_jobs.update(job_id, {"progress": 95})
        
        # Check if plan limit was reached during extraction
        job_data = await extraction_jobs.get(job_id, {})
        if extraction_limit["limit_reached"]:
            balance = extraction_limit.get("credit_balance") or extraction_limit.get("remaining_credits", 0)
            await extraction_jobs.update(job_id, {
                "status": "completed",
                "progress": 100,
                "result": {
                    "status": "plan_limit_reached",
                    "message": f"Insufficient credits for extraction (balance: {balance}). Add credits or upgrade your plan to continue.",
                    "data": [],
                    "plan_info": extraction_limit,
                    "file_key": file_key,
                    "document_type": document_type,
                    "document_description": document_description,
                    "show_upgrade_option": extraction_limit.get("show_upgrade_option", True),
                    "destination": job_data.get("destination"),
                    "destination_name": (
                        updated_destination_name if updated_destination_name else (
                            object_name
                            if not job_data.get("destination_name") or str(job_data.get("destination_name")).strip().lower() == "unknown"
                            else job_data.get("destination_name")
                        )
                    ),
                    "source_type": job_data.get("source_type"),
                    "classification": job_data.get("classification"),
                    "intent": job_data.get("intent"),
                }
            })
            return
        
        # Credit cap: keep partial rows and continue through normal persist + completion.
        # (Previously this path returned early without saving extraction_results, which
        # blocked the agent on "_await_extraction_persisted" / "not ready yet".)
        from utils.extraction_limit_message import (
            credit_cap_applied,
            credit_cap_extraction_message,
        )

        record_count = len(flat_data) if isinstance(flat_data, list) else 1
        limitation_applied = credit_cap_applied(
            original_count=original_file_records or 0,
            can_extract=extraction_limit.get("can_extract") or 0,
        )
        credit_limit_message = None
        if limitation_applied:
            credit_limit_message = credit_cap_extraction_message(
                extracted_count=record_count,
                original_count=int(original_file_records or 0),
            )
            print(f"ℹ️ [FILE EXTRACTION] {credit_limit_message}")
        
        # Track file upload usage using new usage tracking system
        file_size_mb = 0  # We don't have file size in this context, but it's not critical
        await get_entitlements().track_file_upload_usage(str(user_id), file_size_mb)
        
        # Note: For cleanup intent, extraction_rules (data_cleanup_rules, deduplication_rules, filter_rules)
        # are already applied via saved_extraction_rules in extract_structured_data_from_file,
        # so no additional processing needed here
        
        # Check if this is a reusable flow
        if flow_id:
            try:
                # Get saved field mappings
                field_mappings = await get_saved_field_mappings(flow_id, user_id)
                if not field_mappings:
                    raise Exception("No field mappings found for this flow")
                
                # Transform mappings for migration (with actual data keys for case-insensitive matching)
                actual_data_keys = list(flat_data[0].keys()) if flat_data and len(flat_data) > 0 else None
                job_data = await extraction_jobs.get(job_id, {})
                dest_type_for_flow = (job_data.get("destination") or "salesforce").lower()
                dest_name_for_flow = job_data.get("destination_name")

                user_uuid = UUID(user_id)
                flow_details = await get_flow_details(flow_id, user_uuid)
                flow_info = flow_details.get("flow") if flow_details else {}
                schema_data = flow_details.get("schema") if flow_details else {}

                dest_id = flow_info.get("org_id")
                resolved_dest_type, resolved_dest_name = await _resolve_destination_context(
                    user_uuid,
                    flow_info.get("destination_type"),
                    dest_id,
                )
                if resolved_dest_type:
                    dest_type_for_flow = resolved_dest_type
                if resolved_dest_name:
                    dest_name_for_flow = resolved_dest_name

                transformed_mapping = transform_mappings_for_migration(
                    field_mappings,
                    actual_data_keys,
                    dest_type_for_flow,
                )
                
                # Save extraction result FIRST so extraction_result_records is populated for background migration
                has_cleanup_rules = False
                has_transformation_rules = False
                if flat_data and isinstance(flat_data, list) and len(flat_data) > 0:
                    fr = flat_data[0]
                    if isinstance(fr, dict):
                        has_cleanup_rules = "data_cleanup_rules" in fr
                        has_transformation_rules = "transformation_rules" in fr

                await ExtractionResultsService.save_extraction_result(
                    job_id=job_id,
                    user_id=user_id,
                    extracted_data=flat_data,
                    source_type=job_data.get("source_type", "file"),
                    extraction_goals=extraction_goals,
                    document_type=document_type,
                    document_description=document_description,
                    source_name=file_key if job_data.get("source_type") == "file" else None,
                    source_identifier=file_key if job_data.get("source_type") == "file" else None,
                    parent_job_id=job_data.get("parent_job_id"),
                    conversation_id=conversation_id,
                    has_cleanup_rules=has_cleanup_rules,
                    has_transformation_rules=has_transformation_rules,
                    status="success",
                )
                print(f"✅ [FILE FLOW] Saved extraction result for job_id={job_id}, starting background migration")

                await extraction_jobs.update(job_id, {
                    "status": "migration_in_progress",
                    "progress": 95,
                    "result": {
                        "status": "migration_started",
                        "message": "Migration started. Records will be migrated in the background.",
                        "flow_id": flow_id,
                        "document_type": document_type,
                        "record_count": record_count,
                        "destination": dest_type_for_flow,
                        "destination_name": dest_name_for_flow,
                        "source_type": job_data.get("source_type"),
                        "classification": job_data.get("classification"),
                        "intent": job_data.get("intent"),
                    }
                })

                await create_limited_task(
                    process_file_flow_migration_background(
                        job_id=job_id,
                        flow_id=flow_id,
                        user_id=user_id,
                        file_key=file_key,
                        conversation_id=conversation_id,
                        extraction_goals=extraction_goals,
                        document_type=document_type,
                        document_description=document_description,
                        extraction_rules=extraction_rules,
                        mapping_context_summary=mapping_context_summary,
                    )
                )
            except Exception as migration_error:
                await extraction_jobs.update(job_id, {
                    "status": "failed",
                    "progress": 0,
                    "error": f"Migration failed: {str(migration_error)}",
                    "result": None
                })
                return
        else:
            # Regular flow - return data for frontend review
            record_count = len(flat_data) if isinstance(flat_data, list) else 1
            job_data = await extraction_jobs.get(job_id, {})
            # Don't store full data in Redis when over threshold; frontend fetches from API
            store_data = flat_data if record_count <= REDIS_SLIM_RESULT_THRESHOLD else None

            # Run data quality analysis on extracted data
            data_quality_report = None
            try:
                # Only run data quality analysis if we have valid structured data
                # Skip for empty data, single raw_text records (unstructured), or minimal data
                if flat_data and isinstance(flat_data, list) and len(flat_data) > 0:
                    # Check if data has actual structured fields (not just raw_text)
                    first_record = flat_data[0] if isinstance(flat_data[0], dict) else {}
                    has_structured_fields = any(
                        key not in ['raw_text', 'document_type', 'document_description', 
                                   'object_name', 'data_cleanup_rules', 'transformation_rules', 
                                   'filter_rules', 'deduplication_rules', 'mapping_context_summary']
                        for key in first_record.keys()
                    )
                    
                    if has_structured_fields and len(flat_data) > 0:
                        from utils.data_quality_analyzer import analyze_data_quality
                        data_quality_report = await asyncio.to_thread(analyze_data_quality, flat_data)
                        print(f"📊 Data quality analysis completed: Score {data_quality_report.get('summary', {}).get('data_quality_score', 0)}/100")
                    else:
                        print(f"⏭️ Skipping data quality analysis: data is unstructured or minimal (logo/image with text only)")
                else:
                    print(f"⏭️ Skipping data quality analysis: no data to analyze")
            except Exception as quality_error:
                print(f"⚠️ Error running data quality analysis: {quality_error}")
                # Continue without quality report if analysis fails
            
            # Log extraction_completed event if conversation_id is provided
            if conversation_id:
                try:
                    from handlers.classification_handler import create_intent_state
                    
                    # Get source_name based on source_type
                    # For file type: extract filename from file_key (remove {job_id}_ prefix)
                    # For other types: use source_name from extraction_jobs or classification
                    job_data = await extraction_jobs.get(job_id, {})
                    # File path: source is always the uploaded file (not stale Redis job_data).
                    active_source = "file"
                    source_name = file_key.split('_', 1)[1] if '_' in file_key else file_key
                    
                    active_destination = job_data.get("destination", "unknown")
                    destination_name_final = job_data.get("destination_name")
                    
                    # Get classification info for intent_state
                    classification_result = job_data.get("classification", {})
                    active_intent = classification_result.get("intent", "extract") if classification_result else "extract"
                    
                    # Update intent_state - mark as completed but still active (awaiting mapping/migration)
                    intent_state = create_intent_state(
                        active_intent=active_intent,
                        active_source=active_source,
                        active_destination=active_destination,
                        source_name=source_name,
                        destination_name=destination_name_final,
                        job_id=job_id,
                        state="extraction_completed"
                    )

                    from utils.document_category_match import enrich_extraction_category_fields

                    category_meta = await enrich_extraction_category_fields(
                        user_id,
                        active_destination,
                        destination_name_final,
                        document_type,
                        document_description,
                        flat_data,
                    )
                    doc_type_for_meta = category_meta.get("canonical_document_type") or document_type
                    doc_desc_for_meta = category_meta.get("canonical_document_description") or document_description
                    
                    extraction_event_data = {
                        "job_id": job_id,
                        "source_type": active_source,
                        "source_name": source_name,
                        "record_count": record_count,
                        "status": "success",
                        "destination": active_destination,
                        "destination_name": destination_name_final,
                        "file_key": file_key,
                        "document_type": doc_type_for_meta,
                        "document_description": doc_desc_for_meta,
                        "extraction_rules": extraction_rules if extraction_rules else None,
                        "mapping_context_summary": mapping_context_summary,
                        "intent_state": intent_state,
                        "data_quality_report": data_quality_report,
                        **category_meta,
                    }
                    if limitation_applied:
                        extraction_event_data["limitation_applied"] = True
                        extraction_event_data["original_file_records"] = original_file_records
                        extraction_event_data["plan_info"] = extraction_limit
                        if credit_limit_message:
                            extraction_event_data["message"] = credit_limit_message
                    extraction_completed_message = await ChatService.log_data_event(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        event_type="extraction_completed",
                        event_data=extraction_event_data,
                    )
                    # Store message_id and intent_state in extraction_jobs result so it's returned in job status (Redis-backed)
                    if extraction_completed_message and extraction_completed_message.get("id"):
                        job_data = await extraction_jobs.get(job_id, {})
                        result = job_data.get("result") or {}
                        result["extraction_message_id"] = extraction_completed_message["id"]
                        result["intent_state"] = intent_state
                        result["mapping_context_summary"] = mapping_context_summary
                        result["data_quality_report"] = data_quality_report
                        await extraction_jobs.update(job_id, {"result": result})
                    try:
                        from services.pipeline_learning.capture_hooks import schedule_extraction_capture

                        job_data_learn = await extraction_jobs.get(job_id, {}) or {}
                        schema_keys = (
                            list(flat_data[0].keys())
                            if flat_data and isinstance(flat_data[0], dict)
                            else None
                        )
                        schedule_extraction_capture(
                            user_id=str(user_id),
                            conversation_id=str(conversation_id) if conversation_id else None,
                            job_id=job_id,
                            ai_rules=extraction_rules,
                            final_rules=extraction_rules,
                            source_type=job_data_learn.get("source_type", "file"),
                            destination_type=job_data_learn.get("destination"),
                            source_schema=schema_keys,
                            prompt=extraction_goals or "",
                            mapping_context_summary=mapping_context_summary,
                        )
                    except Exception:
                        pass
                    try:
                        from services.conversation.session_sync import sync_session_after_extraction

                        await sync_session_after_extraction(
                            str(user_id),
                            str(conversation_id),
                            job_id=job_id,
                            intent_state=intent_state,
                        )
                    except Exception as sync_exc:
                        print(f"⚠️ Failed to sync conversation session: {sync_exc}")
                except Exception as e:
                    print(f"⚠️ Failed to log extraction_completed event: {e}")

            # Persist to DB before terminal Redis status so SSE extraction_job_finished can load /records.
            has_cleanup_rules = False
            has_transformation_rules = False
            if flat_data and isinstance(flat_data, list) and len(flat_data) > 0:
                first_record = flat_data[0]
                if isinstance(first_record, dict):
                    has_cleanup_rules = "data_cleanup_rules" in first_record
                    has_transformation_rules = "transformation_rules" in first_record

            job_data = await extraction_jobs.get(job_id, {})
            parent_job_id = job_data.get("parent_job_id")
            source_type = job_data.get("source_type", "file")
            source_name = file_key if source_type == "file" else None
            source_identifier = file_key if source_type == "file" else None

            try:
                from utils.task_limiter import get_global_task_semaphore

                async with get_global_task_semaphore():
                    await ExtractionResultsService.save_extraction_result(
                        job_id=job_id,
                        user_id=user_id,
                        extracted_data=flat_data,
                        source_type=source_type,
                        extraction_goals=extraction_goals,
                        document_type=document_type,
                        document_description=document_description,
                        source_name=source_name,
                        source_identifier=source_identifier,
                        parent_job_id=parent_job_id,
                        conversation_id=conversation_id,
                        has_cleanup_rules=has_cleanup_rules,
                        has_transformation_rules=has_transformation_rules,
                        status="success",
                    )
                print(f"✅ Saved extraction result for job_id: {job_id}")
            except Exception as save_error:
                print(f"⚠️ Failed to save extraction result: {save_error}")

            result_payload: Dict[str, Any] = {
                "status": "success",
                "message": credit_limit_message or "File uploaded and processed successfully",
                "data": store_data,
                "record_count": record_count,
                "total_records": record_count,
                "file_key": file_key,
                "document_type": document_type,
                "document_description": document_description,
                "extraction_rules": extraction_rules if extraction_rules else None,
                "show_upgrade_option": show_upgrade_option,
                "destination": job_data.get("destination"),
                "destination_name": job_data.get("destination_name"),
                "source_type": job_data.get("source_type"),
                "classification": job_data.get("classification"),
                "intent": job_data.get("intent"),
            }
            if limitation_applied:
                result_payload["limitation_applied"] = True
                result_payload["original_file_records"] = original_file_records
                result_payload["total_in_file"] = original_file_records
                result_payload["plan_info"] = extraction_limit
            if data_quality_report:
                result_payload["data_quality_report"] = data_quality_report
            job_after_log = await extraction_jobs.get(job_id, {})
            logged_result = job_after_log.get("result")
            if isinstance(logged_result, dict):
                for key in (
                    "extraction_message_id",
                    "intent_state",
                    "mapping_context_summary",
                ):
                    if logged_result.get(key) is not None:
                        result_payload[key] = logged_result[key]

            await extraction_jobs.update(
                job_id,
                {
                    "status": "completed",
                    "progress": 100,
                    "result": result_payload,
                },
            )

    except Exception as e:
        # Provide user-friendly error messages (apply before logging/explaining)
        error_message = str(e)
        if "503" in error_message or "Service Temporarily Unavailable" in error_message:
            error_message = "AI service is temporarily unavailable. Please try again in a few minutes."
        elif "502" in error_message or "Bad gateway" in error_message:
            error_message = "AI processing service is experiencing issues. Please try again later."
        elif "429" in error_message or "rate limit" in error_message.lower():
            error_message = "Rate limit exceeded. Please wait a moment before trying again."
        elif "401" in error_message or "unauthorized" in error_message.lower():
            error_message = "Authentication failed. Please refresh the page and try again."
        elif "timeout" in error_message.lower():
            error_message = "Request timed out. Please try again with a smaller file."
        elif "connection" in error_message.lower():
            error_message = "Network connection error. Please check your internet connection and try again."

        # Log extraction_completed (failed) event if conversation_id is provided
        if conversation_id:
            try:
                job_data = await extraction_jobs.get(job_id, {})
                source_name = job_data.get("source_name") or job_data.get("file_key")
                await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type="extraction_completed",
                    event_data={
                        "job_id": job_id,
                        "source_type": job_data.get("source_type", "file"),
                        "source_name": source_name,
                        "record_count": 0,
                        "status": "failed",
                        "error": error_message,
                    }
                )
            except Exception as log_error:
                print(f"⚠️ Failed to log extraction_completed (failed) event: {log_error}")

        # Get AI-generated explanation (non-blocking, with timeout)
        explanation_result = {"explanation": None, "solution": None}
        try:
            from services.error_explanation_service import explain_error

            job_data_for_ctx = await extraction_jobs.get(job_id, {}) or {}
            explanation_result = await explain_error(
                raw_error=error_message,
                source=job_data_for_ctx.get("source_type", "file"),
                context={"operation": "extraction", "user_query": job_data_for_ctx.get("extraction_goals")},
            )
        except Exception as explain_err:
            print(f"⚠️ Error explanation failed: {explain_err}")

        await extraction_jobs.update(job_id, {
            "status": "failed",
            "progress": 0,
            "error": error_message,
            "error_explanation": explanation_result.get("explanation"),
            "error_solution": explanation_result.get("solution"),
            "result": None,
        })
        
        # Save failed extraction result to database (for debugging)
        try:
            # Get job data (for both parent_job_id and source information)
            job_data = await extraction_jobs.get(job_id, {}) or {}
            parent_job_id = job_data.get("parent_job_id") if job_data else None
            source_type = job_data.get("source_type", "file") if job_data else "file"
            source_name = job_data.get("file_key") if (job_data and source_type == "file") else None
            source_identifier = job_data.get("file_key") if (job_data and source_type == "file") else None
            
            # Use limited task to prevent overwhelming the system
            await create_limited_task(
                ExtractionResultsService.save_extraction_result(
                    job_id=job_id,
                    user_id=user_id,
                    extracted_data=[],  # Empty data for failed extractions
                    source_type=source_type,
                    extraction_goals=job_data.get("extraction_goals"),
                    document_type=None,
                    document_description=None,
                    source_name=source_name,
                    source_identifier=source_identifier,
                    parent_job_id=parent_job_id,
                    conversation_id=conversation_id,
                    has_cleanup_rules=False,
                    has_transformation_rules=False,
                    status="failed",
                    error_message=error_message
                )
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save failed extraction result: {save_error}")


# Initialize S3 client
s3_client = boto3.client('s3')


BUCKET_NAME = 'edisonaistorage'  # Replace with your S3 bucket name
