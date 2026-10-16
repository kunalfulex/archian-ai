"""
Google Sheets Handler Module

This module contains all Google Sheets related functions for:
- Data extraction from Google Sheets
- Information analysis of Google Sheets
- Spreadsheet detection and metadata handling
- Background processing for Google Sheets operations
"""

import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from googleapiclient.errors import HttpError

from utils.extraction_sse_payload import extraction_setup_progress_sse_line

# In-memory storage for pending update confirmations
# In production, this should be in Redis or database with TTL
pending_google_sheets_updates = {}


async def process_google_sheets_extraction_background(job_id: str, spreadsheet_id: str, spreadsheet_name: str, 
                                                   sheet_name: str, range_name: str, extraction_goals: str, 
                                                   user_id: str, google_sheets_connection_id: str,
                                                   destination: str = "salesforce", destination_name: str = None,
                                                   destination_sheet_name: Optional[str] = None,
                                                   conversation_id: str = None, parent_job_id: str = None,
                                                   extraction_rules: Optional[Dict[str, Any]] = None,
                                                   extracted_data: Optional[List[Dict[str, Any]]] = None,
                                                   extract_field_scope: Optional[Dict[str, Any]] = None):
    """
    Background task to process Google Sheets extraction using existing file extraction logic
    Converts Google Sheets data to CSV format and uses extract_structured_data_from_file
    
    Args:
        job_id: Unique job identifier
        spreadsheet_id: Google Spreadsheet ID
        spreadsheet_name: Name of the spreadsheet
        sheet_name: Name of the sheet/tab
        range_name: Cell range to extract
        extraction_goals: User's extraction goals
        user_id: User ID
        google_sheets_connection_id: Google Sheets connection ID
        destination: Target destination (salesforce, google_sheets, sql, etc.)
        destination_name: Specific destination name (org name, spreadsheet name, etc.)
    """
    try:
        import tempfile
        import pandas as pd
        from utils.job_stores import extraction_jobs
        
        # Lazy import to avoid circular dependency
        from services.architect_agent import extract_structured_data_from_file
        
        # Update job status to processing (Redis-backed)
        # Use update() to preserve existing fields (like cleanup_extraction_rules from cleanup handler)
        # Note: Job may already exist if created synchronously in flow rerun or by cleanup handler
        
        # Get existing job to check for cleanup intent, rules, and cancellation
        existing_job = await extraction_jobs.get(job_id) or {}
        if existing_job.get("cancelled"):
            print(f"🛑 Google Sheets extraction job {job_id} cancelled by user")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return
        
        # Check if this is a cleanup intent and get cleanup_extraction_rules
        # Priority: 1. existing_job (set by cleanup handler), 2. extraction_rules parameter, 3. parent_job
        cleanup_rules = None
        intent_value = None
        
        condition_check = existing_job.get("intent") == "cleanup" or extraction_rules
        if condition_check:
            # ✅ Check existing_job first (the job created by cleanup handler)
            cleanup_rules = existing_job.get("cleanup_extraction_rules") or extraction_rules
            if cleanup_rules:
                intent_value = "cleanup"
        elif parent_job_id:
            # Fallback: check parent_job (for backward compatibility)
            parent_job = await extraction_jobs.get(parent_job_id, {})
            if parent_job.get("intent") == "cleanup":
                cleanup_rules = parent_job.get("cleanup_extraction_rules")
                if cleanup_rules:
                    intent_value = "cleanup"
        
        # Prepare update data
        update_data = {
            "status": "processing",
            "progress": 0,
            "result": None,
            "error": None,
            "created_at": existing_job.get("created_at") or datetime.now().isoformat(),
            "extraction_goals": extraction_goals,
            "source_type": "google_sheets",
            "spreadsheet_name": spreadsheet_name,
            "sheet_name": sheet_name,
            "destination": destination,
            "destination_name": destination_name,
            "parent_job_id": parent_job_id,
            "user_id": existing_job.get("user_id") or user_id,
            "job_type": existing_job.get("job_type") or "google_sheets_extraction",
        }
        
        # Add cleanup-specific fields if present
        if intent_value:
            update_data["intent"] = intent_value
        if cleanup_rules:
            update_data["cleanup_extraction_rules"] = cleanup_rules
        
        # Use update() to preserve all existing fields automatically
        await extraction_jobs.update(job_id, update_data)
        
        # Check if this is cleanup intent with existing extracted data (passed as parameter or in job metadata)
        # Priority: 1. Parameter, 2. Job metadata
        cleanup_extracted_data = extracted_data
        if not cleanup_extracted_data:
            job_data_check = await extraction_jobs.get(job_id, {})
            cleanup_extracted_data = job_data_check.get("extracted_data")
        
        print(f"🔍 [CLEANUP FLOW] Intent check: intent={intent_value or existing_job.get('intent')}, has_extracted_data={bool(cleanup_extracted_data)}")
        
        # Progress callback function (needed for both cleanup and normal extraction flows)
        import asyncio as _asyncio
        async_jobs_ref = extraction_jobs
        job_id_ref = job_id
        
        def update_progress(progress_percent, message=""):
            # Schedule async update (fire-and-forget from sync context)
            # Since we're in an async function, we can use create_task directly
            _asyncio.create_task(async_jobs_ref.update(job_id_ref, {"progress": progress_percent}))
        
        # Initialize variables that will be used in unified flow
        temp_csv_path = None
        saved_extraction_rules = None
        quality_report_rules = None  # For cleanup flow: quality report rules to merge with prompt rules
        
        # Determine if this is cleanup flow with extracted data
        # Use intent_value or check existing_job (after our update, existing_job might be outdated, so check intent_value first)
        current_intent = intent_value or existing_job.get("intent")
        is_cleanup_flow = current_intent == "cleanup" and cleanup_extracted_data
        
        if is_cleanup_flow:
            # Cleanup flow: Apply cleanup rules, then route through normal extraction pipeline
            print(f"🔍 [CLEANUP FLOW] ✅ ENTERING CLEANUP FLOW")
            print(f"🔍 [CLEANUP FLOW] Using destination: destination={destination}, destination_name={destination_name}")
            
            update_progress(10, "Processing cleanup data...")
            
            flat_data = cleanup_extracted_data
            
            # Get existing cleanup rules from job metadata (from quality report)
            # These will be merged with prompt-generated rules after extract_structured_data_from_file
            # Use cleanup_rules we already extracted, or check existing_job
            quality_report_rules = cleanup_rules or existing_job.get("cleanup_extraction_rules", {})
            
            update_progress(20, "Converting data to CSV format...")
            
            # Convert data to CSV format (same as normal flow does with Google Sheets data)
            # NOTE: We don't apply cleanup rules here - they will be applied by extract_structured_data_from_file
            # Create DataFrame from data (rules will be applied later in unified processing)
            df = pd.DataFrame(flat_data)
            
            # Create temporary CSV file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as temp_csv:
                df.to_csv(temp_csv.name, index=False)
                temp_csv_path = temp_csv.name
            
            # Don't pass saved_extraction_rules - let extract_structured_data_from_file generate rules from prompt
            # We'll merge quality report rules with prompt-generated rules after extraction
            saved_extraction_rules = None
        
        else:
            # Normal extraction flow - extract from Google Sheets
            # Log extraction_started event with intent_state
            if conversation_id:
                try:
                    from services.chat_service import ChatService
                    from handlers.classification_handler import create_intent_state
                    
                    # Determine intent (extract or migrate based on destination)
                    active_intent = "migrate" if destination and destination != "unknown" else "extract"
                    
                    intent_state = create_intent_state(
                        active_intent=active_intent,
                        active_source="google_sheets",
                        active_destination=destination or "unknown",
                        source_name=spreadsheet_name,
                        destination_name=destination_name,
                        job_id=job_id,
                        state="extracting"
                    )
                    
                    await ChatService.log_data_event(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        event_type="extraction_started",
                        event_data={
                            "job_id": job_id,
                            "source_type": "google_sheets",
                            "source_name": spreadsheet_name,
                            "destination": destination,
                            "destination_name": destination_name,
                            "intent_state": intent_state
                        }
                    )
                except Exception as e:
                    print(f"⚠️ Failed to log extraction_started event: {e}")
            
            update_progress(10, "Connecting to Google Sheets...")
            
            # Get Google Sheets connection details
            from db.connection import database
            google_sheets_connection = await database.fetch_one(
                """
                SELECT access_token, refresh_token, expires_at
                FROM google_sheets_connections 
                WHERE id = :connection_id AND is_active = true
                """,
                {"connection_id": google_sheets_connection_id}
            )
            
            if not google_sheets_connection:
                raise Exception("Google Sheets connection not found or inactive")
            
            update_progress(20, "Initializing Google Sheets service...")
            
            # Initialize Google Sheets Service
            from services.google_sheets_service import GoogleSheetsService
            
            sheets_service = GoogleSheetsService(
                access_token=google_sheets_connection['access_token'],
                refresh_token=google_sheets_connection['refresh_token'],
                expires_at=google_sheets_connection['expires_at']
            )
            
            # Get user's extraction limit BEFORE reading from Google Sheets
            from services.architect_agent import get_user_extraction_limit
            extraction_limit = await get_user_extraction_limit(user_id)
            
            # Check if plan limit is reached
            if extraction_limit.get("limit_reached", False) or extraction_limit.get("can_extract", 0) == 0:
                # Plan limit reached - return early
                await extraction_jobs.update(job_id, {
                    "status": "completed",
                    "progress": 100,
                    "result": {
                        "status": "plan_limit_reached",
                        "message": f"Insufficient credits for extraction (balance: {extraction_limit.get('credit_balance') or extraction_limit.get('remaining_credits', 0)}). Add credits or upgrade your plan.",
                        "data": [],
                        "total_records": 0,
                        "plan_info": {
                            "plan_type": extraction_limit.get("plan_type"),
                            "credit_balance": extraction_limit.get("credit_balance"),
                            "remaining_credits": extraction_limit.get("remaining_credits"),
                            "can_extract": extraction_limit.get("can_extract", 0),
                            "is_plan_limit_reached": True
                        },
                        "show_upgrade_option": True
                    }
                })
                return
            
            # Parse user explicit limit from prompt (e.g., "extract 10 records", "first 50 records")
            import re
            user_explicit_limit = None
            limit_patterns = [
                r'\b(?:only|extract|get|fetch)\s+(\d+)\s+records?\b',
                r'\b(?:first|last)\s+(\d+)\s+records?\b',
                r'\b(\d+)\s+records?\s+(?:only|from|of)',
            ]
            for pattern in limit_patterns:
                match = re.search(pattern, extraction_goals.lower())
                if match:
                    try:
                        user_explicit_limit = int(match.group(1))
                        print(f"📊 Found user explicit limit in prompt: {user_explicit_limit}")
                        break
                    except ValueError:
                        continue
            
            # Credit-based: effective limit = min(user explicit limit, can_extract)
            can_extract = extraction_limit.get("can_extract", 0)
            effective_limit = can_extract if can_extract > 0 else None
            if user_explicit_limit and effective_limit is not None:
                effective_limit = min(effective_limit, user_explicit_limit)
                print(f"📊 Effective limit: {effective_limit} (user requested {user_explicit_limit}, credits allow {can_extract})")
            elif effective_limit is not None:
                print(f"📊 Using credit-based limit: {effective_limit} records")
            elif user_explicit_limit:
                effective_limit = user_explicit_limit
                print(f"📊 Using user explicit limit: {effective_limit}")
            
            update_progress(30, f"Reading data from Google Sheets{f' (limit: {effective_limit} records)' if effective_limit else ''}...")
            
            # Extract data from Google Sheets with plan limit applied at API level
            result = await sheets_service.read_spreadsheet(
                spreadsheet_id=spreadsheet_id,
                range_name=range_name,
                sheet_name=sheet_name,
                max_rows=effective_limit  # Apply limit at API level (None for unlimited plans without user limit)
            )
            
            if not result.get('success', False):
                raise Exception(f"Failed to read Google Sheets: {result.get('error', 'Unknown error')}")
            
            data = result.get('data', [])
            if not data:
                raise Exception("No data found in the specified range")
            
            update_progress(40, f"Extracted {len(data)} rows from Google Sheets")
            
            # Convert Google Sheets data to CSV format for existing file extraction logic
            update_progress(50, "Converting to CSV format...")
            
            # Create DataFrame from Google Sheets data
            df = pd.DataFrame(data)

            from utils.gsheets_column_selection import (
                build_keep_only_transformation_rules,
                restrict_dataframe_to_prompt_columns,
            )

            df, kept_columns = restrict_dataframe_to_prompt_columns(
                df,
                extraction_goals,
                source_name=spreadsheet_name,
            )
            from services.planning.extract_field_scope import (
                filter_gsheets_columns_for_scope,
                resolve_extract_field_scope,
            )

            field_scope = resolve_extract_field_scope(
                extract_field_scope,
                source_type="google_sheets",
                destination=destination,
                source_name=spreadsheet_name,
                destination_name=destination_name,
            )
            if field_scope.get("cross_system"):
                allowed_headers = filter_gsheets_columns_for_scope(
                    [str(c) for c in df.columns],
                    field_scope,
                    prompt=extraction_goals,
                )
                if allowed_headers and len(allowed_headers) < len(df.columns):
                    df = df[allowed_headers].copy()
                    if kept_columns:
                        kept_columns = [c for c in kept_columns if c in allowed_headers]
                    print(
                        f"📋 [GSHEETS] Cross-system scope removed id/fk columns; "
                        f"kept {len(allowed_headers)} column(s)"
                    )
            column_keep_rules = None
            if kept_columns:
                print(
                    f"📋 [GSHEETS] Restricting to {len(kept_columns)} column(s) from prompt: "
                    f"{', '.join(kept_columns)}"
                )
                column_keep_rules = build_keep_only_transformation_rules(kept_columns)

            # Create temporary CSV file
            with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as temp_csv:
                df.to_csv(temp_csv.name, index=False)
                temp_csv_path = temp_csv.name
            
            # For flow rerun: Use extraction_rules parameter if provided
            if extraction_rules:
                saved_extraction_rules = extraction_rules
            elif column_keep_rules:
                saved_extraction_rules = column_keep_rules
            else:
                saved_extraction_rules = None
        
        # Unified processing path - both cleanup and normal flows merge here
        update_progress(60, "Processing with existing file extraction logic...")

        job_check = await extraction_jobs.get(job_id, {})
        if job_check.get("cancelled"):
            print(f"🛑 Google Sheets extraction job {job_id} cancelled by user")
            await extraction_jobs.update(job_id, {"status": "cancelled"})
            return

        # Ensure database is imported (needed for post-processing in both flows)
        from db.connection import database

        # Use existing file extraction logic (this handles all transformation rules, OpenAI processing, etc.)
        # Pass saved_extraction_rules if available (from cleanup or flow rerun)
        job_profile = existing_job.get("extraction_profile")
        job_template = existing_job.get("template")
        flat_data, document_type, _, document_description, extraction_limit, original_file_records, show_upgrade_option, object_name, updated_destination_name, extraction_rules_result, mapping_context_summary = await extract_structured_data_from_file(
            local_file_path=temp_csv_path,
            extraction_goals=extraction_goals,
            user_id=user_id,
            progress_callback=lambda progress, msg: update_progress(60 + (progress * 0.3), f"Processing: {msg}"),
            destination=destination,
            destination_name=destination_name,
            source="google_sheets",
            source_name=spreadsheet_name,
            saved_extraction_rules=saved_extraction_rules,
            extraction_profile=job_profile,
            intent=intent_value,
            template=job_template,
        )
        
        # Use extraction_rules_result from extraction (which will be saved rules if provided, or OpenAI rules if not)
        extraction_rules = extraction_rules_result or saved_extraction_rules
        
        # For cleanup flow: Merge quality report rules with prompt-generated rules
        # This preserves all rules from previous operations and adds new cleanup rules
        if is_cleanup_flow and quality_report_rules:
            # Initialize extraction_rules_result from prompt-generated rules
            extraction_rules_result_merged = extraction_rules if extraction_rules else {}
            if not extraction_rules_result_merged:
                extraction_rules_result_merged = {}
            
            # Merge quality report rules with prompt-generated rules
            # Prompt rules take precedence for filter_rules, deduplication_rules, transformation_rules
            # Quality report data_cleanup_rules should be merged (not overwritten)
            if quality_report_rules.get("data_cleanup_rules"):
                existing_data_cleanup = extraction_rules_result_merged.get("data_cleanup_rules", {})
                if isinstance(existing_data_cleanup, dict) and isinstance(quality_report_rules["data_cleanup_rules"], dict):
                    # Merge both dicts - prompt rules take precedence if there are conflicts
                    extraction_rules_result_merged["data_cleanup_rules"] = {**quality_report_rules["data_cleanup_rules"], **existing_data_cleanup}
                else:
                    # If not both dicts, use quality report rules as fallback
                    if not existing_data_cleanup:
                        extraction_rules_result_merged["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
            
            # Use quality report rules for other rule types only if prompt didn't generate them
            if quality_report_rules.get("filter_rules") and not extraction_rules_result_merged.get("filter_rules"):
                extraction_rules_result_merged["filter_rules"] = quality_report_rules["filter_rules"]
            if quality_report_rules.get("deduplication_rules") and not extraction_rules_result_merged.get("deduplication_rules"):
                extraction_rules_result_merged["deduplication_rules"] = quality_report_rules["deduplication_rules"]
            if quality_report_rules.get("transformation_rules") and not extraction_rules_result_merged.get("transformation_rules"):
                extraction_rules_result_merged["transformation_rules"] = quality_report_rules["transformation_rules"]
            
            # Now accumulate with original_extraction_rules to preserve original transformation rules
            # Get existing extraction rules - prioritize original_extraction_rules from job metadata
            existing_extraction_rules = {}
            source_schema = None
            
            # First, try to get original_extraction_rules from job metadata (from initial extraction)
            job_data = await extraction_jobs.get(job_id, {})
            original_extraction_rules_from_job = job_data.get("original_extraction_rules")
            
            # Defensive check: ensure original_extraction_rules_from_job is a dict (not a list, None, or other type)
            if original_extraction_rules_from_job is not None and isinstance(original_extraction_rules_from_job, dict) and len(original_extraction_rules_from_job) > 0:
                existing_extraction_rules = original_extraction_rules_from_job
                # Additional defensive check: ensure existing_extraction_rules is still a dict and not empty
                if not isinstance(existing_extraction_rules, dict) or not existing_extraction_rules:
                    print(f"⚠️ [GOOGLE SHEETS CLEANUP FLOW] existing_extraction_rules is not a valid dict (type: {type(existing_extraction_rules)}, empty: {not existing_extraction_rules if isinstance(existing_extraction_rules, dict) else 'N/A'}), resetting to empty dict")
                    existing_extraction_rules = {}
                else:
                    transformation_rules = existing_extraction_rules.get('transformation_rules')
                    if transformation_rules is not None and not isinstance(transformation_rules, dict):
                        print(f"⚠️ [GOOGLE SHEETS CLEANUP FLOW] transformation_rules exists but is not a dict (type: {type(transformation_rules)}), will not preserve transformation rules")
            elif original_extraction_rules_from_job is not None:
                # original_extraction_rules exists but is not a dict (could be a list, empty dict, or other type)
                print(f"⚠️ [GOOGLE SHEETS CLEANUP FLOW] original_extraction_rules found but is not a valid dict (type: {type(original_extraction_rules_from_job)}, value: {original_extraction_rules_from_job}), resetting to empty dict")
                existing_extraction_rules = {}
            else:
                # original_extraction_rules is None or not present
                existing_extraction_rules = {}
            
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
                            print(f"⚠️ [GOOGLE SHEETS CLEANUP FLOW] Error getting source_schema from parent job: {e}")
                
                # If still no source_schema, extract from flat_data (original data before transformations)
                if not source_schema and flat_data:
                    from utils.rule_accumulation import extract_source_schema_from_data
                    source_schema = extract_source_schema_from_data(flat_data)
            
            # Accumulate merged rules with original transformation rules
            if source_schema and existing_extraction_rules:
                from utils.rule_accumulation import accumulate_cleanup_rules
                try:
                    # Combine extraction_rules_result_merged (prompt + quality report) with existing_extraction_rules (original transformation rules)
                    # Use extraction_rules_result_merged as new_cleanup_rules (they already include quality report rules)
                    # Ensure existing_extraction_rules is a dict (will be normalized in accumulate_cleanup_rules)
                    if not isinstance(existing_extraction_rules, dict):
                        existing_extraction_rules = {}
                    extraction_rules = accumulate_cleanup_rules(
                        existing_rules=existing_extraction_rules,
                        new_cleanup_rules=extraction_rules_result_merged,
                        source_schema=source_schema
                    )
                except Exception as acc_error:
                    print(f"⚠️ [GOOGLE SHEETS CLEANUP FLOW] Error accumulating rules: {acc_error}")
                    import traceback
                    traceback.print_exc()
                    # Fallback: use extraction_rules_result_merged (already merged)
                    extraction_rules = extraction_rules_result_merged
            else:
                # Fallback: use extraction_rules_result_merged (already merged prompt + quality report)
                extraction_rules = extraction_rules_result_merged
                if not extraction_rules:
                    extraction_rules = {}
            
            # Apply merged rules to flat_data (especially data_cleanup_rules from quality report)
            if extraction_rules:
                from utils.extraction_utils import apply_all_extraction_rules
                flat_data = apply_all_extraction_rules(
                    flat_data,
                    extraction_rules,
                    extraction_profile=existing_job.get("extraction_profile"),
                    prompt=extraction_goals,
                    intent=intent_value,
                    source="google_sheets",
                )
            
            # Get preserved document_type/document_description from job_data if available (to maintain metadata)
            # This matches Salesforce cleanup flow behavior
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
                from services.extraction_results_service import ExtractionResultsService
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
        if updated_destination_name and (destination_name is None or destination_name == "unknown") and destination == "google_sheets":
            destination_name = updated_destination_name
        
        update_progress(90, "Storing extraction results...")
        
        # Clean up temporary file
        import os
        try:
            os.unlink(temp_csv_path)
        except:
            pass
        
        update_progress(95, "Finalizing extraction...")
        
        # Get the user_google_spreadsheets.id to use as connector_id
        google_spreadsheet_record = await database.fetch_one(
            """
            SELECT id FROM user_google_spreadsheets 
            WHERE user_id = :user_id AND spreadsheet_id = :spreadsheet_id
            ORDER BY last_accessed_at DESC LIMIT 1
            """,
            {"user_id": user_id, "spreadsheet_id": spreadsheet_id}
        )
        
        # Transform extraction_limit to plan_info format (consistent with Salesforce)
        plan_info = None
        if extraction_limit:
            plan_info = {
                "plan_type": extraction_limit.get("plan_type"),
                "credit_balance": extraction_limit.get("credit_balance"),
                "remaining_credits": extraction_limit.get("remaining_credits"),
                "can_extract": extraction_limit.get("can_extract", 0),
                "is_plan_limit_reached": extraction_limit.get("can_extract", 0) == 0 and len(flat_data) == 0
            }
        
        # Determine if limitation was applied (records were limited)
        extracted_count = len(flat_data) if flat_data else 0
        limitation_applied = extraction_limit and (
            extraction_limit.get("can_extract", 0) < original_file_records or
            (extraction_limit.get("can_extract", 0) > 0 and extracted_count < original_file_records)
        )
        
        # Check if plan limit was reached (no records extracted due to limit)
        plan_limit_reached = extraction_limit and extraction_limit.get("can_extract", 0) == 0 and extracted_count == 0
        
        # Prepare final result (same format as Salesforce extraction)
        if plan_limit_reached:
            # Plan limit reached - no records extracted
            final_result = {
                "status": "plan_limit_reached",
                "message": f"Insufficient credits for extraction (balance: {extraction_limit.get('credit_balance') or extraction_limit.get('remaining_credits', 0)}). Add credits or upgrade your plan.",
                "data": [],
                "total_records": 0,
                "source_type": "google_sheets",
                "spreadsheet_name": spreadsheet_name,
                "sheet_name": sheet_name,
                "extraction_goals": extraction_goals,
                "document_type": document_type,
                "document_description": document_description,
                "destination_name": destination_name,
                "plan_info": plan_info,
                "show_upgrade_option": True,
                "original_record_count": original_file_records,
                "extracted_schema_type": "gsheets",
                "connector_id": str(google_spreadsheet_record['id']) if google_spreadsheet_record else None,
                "sql_query": None,
                "table_name": sheet_name,
                "user_prompt": extraction_goals,
                "mapping_context_summary": mapping_context_summary,
            }
        else:
            # Records extracted (may be limited)
            final_result = {
                "status": "success" if limitation_applied else "success",
                "data": flat_data,
                "total_records": extracted_count,
                "source_type": "google_sheets",
                "spreadsheet_name": spreadsheet_name,
                "sheet_name": sheet_name,
                "extraction_goals": extraction_goals,
                "document_type": document_type,
                "destination_name": destination_name,
                "document_description": document_description,
                        "extraction_rules": extraction_rules if extraction_rules else None,
                        "mapping_context_summary": mapping_context_summary,
                "plan_info": plan_info,
                "show_upgrade_option": show_upgrade_option,
                "limitation_applied": limitation_applied,
                "original_record_count": original_file_records,
                # Add fields expected by frontend for Salesforce mapping
                "file_key": None,  # Google Sheets don't have file keys
                "extracted_schema_type": "gsheets",
                "connector_id": str(google_spreadsheet_record['id']) if google_spreadsheet_record else None,
                "sql_query": None,  # Google Sheets don't have SQL queries
                "table_name": sheet_name,  # Tab name — same role as SQL table / SF object for mapping UI
                "user_prompt": extraction_goals
            }
        
        # Run data quality analysis on extracted data
        data_quality_report = None
        try:
            from utils.data_quality_analyzer import analyze_data_quality
            extracted_data = final_result.get("data", [])
            if extracted_data:
                data_quality_report = await asyncio.to_thread(analyze_data_quality, extracted_data)
                print(f"📊 Data quality analysis completed: Score {data_quality_report.get('summary', {}).get('data_quality_score', 0)}/100")
        except Exception as quality_error:
            print(f"⚠️ Error running data quality analysis: {quality_error}")
            # Continue without quality report if analysis fails
        
        # Log extraction_completed event FIRST with intent_state
        # This must happen BEFORE updating job status so extraction_message_id is included in the result
        if conversation_id:
            try:
                from services.chat_service import ChatService
                from handlers.classification_handler import create_intent_state
                
                # Determine intent (extract or migrate based on destination)
                active_intent = "migrate" if destination and destination != "unknown" else "extract"
                destination_name_final = (
                    updated_destination_name if updated_destination_name else (
                        object_name
                        if not destination_name or str(destination_name).strip().lower() == "unknown"
                        else destination_name
                    )
                )
                
                # Update intent_state - mark as completed but still active (awaiting mapping/migration)
                intent_state = create_intent_state(
                    active_intent=active_intent,
                    active_source="google_sheets",
                    active_destination=destination or "unknown",
                    source_name=spreadsheet_name,
                    destination_name=destination_name_final,
                    job_id=job_id,
                    state="extraction_completed"
                )

                from utils.document_category_match import enrich_extraction_category_fields

                sample_data = (
                    (final_result.get("data") or [None])[0]
                    if isinstance(final_result.get("data"), list)
                    else {}
                )
                category_meta = await enrich_extraction_category_fields(
                    user_id,
                    destination,
                    destination_name_final,
                    document_type,
                    document_description,
                    sample_data if isinstance(sample_data, dict) else {},
                )
                if category_meta.get("canonical_document_type"):
                    document_type = category_meta["canonical_document_type"]
                    final_result["document_type"] = document_type
                if category_meta.get("canonical_document_description"):
                    document_description = category_meta["canonical_document_description"]
                    final_result["document_description"] = document_description
                
                extraction_completed_message = await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type="extraction_completed",
                    event_data={
                        "job_id": job_id,
                        "source_type": "google_sheets",
                        "source_name": spreadsheet_name,
                        "record_count": final_result.get("total_records", 0),
                        "status": "success",
                        "destination": destination,
                        "destination_name": destination_name_final,
                        "destination_sheet_name": destination_sheet_name,
                        "document_type": document_type,
                        "document_description": document_description,
                        "connector_id": final_result.get("connector_id"),
                        "extraction_rules": final_result.get("extraction_rules"),
                        "mapping_context_summary": final_result.get("mapping_context_summary"),
                        "intent_state": intent_state,
                        "data_quality_report": data_quality_report,
                        **category_meta,
                    }
                )
                # Store message_id and intent_state in final_result BEFORE updating job status
                # This ensures extraction_message_id is included when job status is saved
                if extraction_completed_message and extraction_completed_message.get("id"):
                    final_result["extraction_message_id"] = extraction_completed_message["id"]
                    final_result["intent_state"] = intent_state
                    final_result["mapping_context_summary"] = final_result.get("mapping_context_summary")
                    final_result["data_quality_report"] = data_quality_report
            except Exception as e:
                print(f"⚠️ Failed to log extraction_completed event: {e}")
        
        # Persist extraction to DB before marking job completed so migration sees full rows
        try:
            from services.extraction_results_service import ExtractionResultsService

            extracted_data = final_result.get("data", [])
            has_cleanup_rules = False
            has_transformation_rules = False
            if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                first_record = extracted_data[0]
                if isinstance(first_record, dict):
                    has_cleanup_rules = "data_cleanup_rules" in first_record
                    has_transformation_rules = "transformation_rules" in first_record

            await ExtractionResultsService.save_extraction_result(
                job_id=job_id,
                user_id=user_id,
                extracted_data=extracted_data,
                source_type="google_sheets",
                extraction_goals=extraction_goals,
                document_type=document_type,
                document_description=document_description,
                source_name=spreadsheet_name,
                source_identifier=spreadsheet_id,
                parent_job_id=parent_job_id,
                conversation_id=conversation_id,
                has_cleanup_rules=has_cleanup_rules,
                has_transformation_rules=has_transformation_rules,
                status="success" if not plan_limit_reached else "plan_limit_reached",
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save Google Sheets extraction result: {save_error}")

        # Don't store full data in Redis when over threshold; frontend fetches from API
        result_for_redis = final_result
        gs_total = final_result.get("total_records", 0)
        if gs_total > 0:
            from utils.redis_service import REDIS_SLIM_RESULT_THRESHOLD
            if gs_total > REDIS_SLIM_RESULT_THRESHOLD:
                result_for_redis = {**final_result, "data": None, "record_count": gs_total}

        # Update job status to completed (Redis-backed) - now includes extraction_message_id in result
        await extraction_jobs.update(job_id, {
            "status": "completed",
            "progress": 100,
            "result": result_for_redis,
            "destination_name": destination_name,  # Update top-level destination_name
            "completed_at": datetime.now().isoformat()
        })

        # Charge extraction fee (idempotent per job_id)
        # Extraction is free; no credit deduction

    except Exception as e:
        print(f"❌ Google Sheets extraction error for job {job_id}: {str(e)}")

        # Get AI-generated explanation (non-blocking, with timeout)
        explanation_result = {"explanation": None, "solution": None}
        try:
            from services.error_explanation_service import explain_error

            explanation_result = await explain_error(
                raw_error=str(e),
                source="google_sheets",
                context={
                    "object_name": spreadsheet_name,
                    "operation": "extraction",
                },
            )
        except Exception as explain_err:
            print(f"⚠️ Error explanation failed: {explain_err}")

        # Update job status to failed (Redis-backed)
        try:
            from utils.job_stores import extraction_jobs
            await extraction_jobs.update(job_id, {
                "status": "failed",
                "progress": 100,
                "error": str(e),
                "error_explanation": explanation_result.get("explanation"),
                "error_solution": explanation_result.get("solution"),
                "failed_at": datetime.now().isoformat(),
            })
        except Exception as update_error:
            print(f"⚠️ Failed to update job status: {update_error}")
        
        # Save failed extraction result to database (for debugging)
        try:
            from services.extraction_results_service import ExtractionResultsService
            
            asyncio.create_task(
                ExtractionResultsService.save_extraction_result(
                    job_id=job_id,
                    user_id=user_id,
                    extracted_data=[],
                    source_type="google_sheets",
                    extraction_goals=extraction_goals if 'extraction_goals' in locals() else None,
                    document_type=None,
                    document_description=None,
                    source_name=spreadsheet_name if 'spreadsheet_name' in locals() else None,
                    source_identifier=spreadsheet_id if 'spreadsheet_id' in locals() else None,
                    parent_job_id=parent_job_id if 'parent_job_id' in locals() else None,
                    conversation_id=conversation_id if 'conversation_id' in locals() else None,
                    has_cleanup_rules=False,
                    has_transformation_rules=False,
                    status="failed",
                    error_message=str(e)
                )
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save failed Google Sheets extraction result: {save_error}")
            # Don't fail if saving fails
        
        raise e


async def seed_gsheets_extracted_schema_for_mapping(
    *,
    user_id: str,
    google_sheets_connection_id: str,
    spreadsheet_id: str,
    spreadsheet_name: str,
    sheet_name: str,
    header_names: List[str],
) -> None:
    """
    Write minimal gsheets_extracted_schemas so map_fields can run immediately after
    spreadsheet creation, without waiting for background AI categorization.
    """
    from db.connection import database

    names = [str(h).strip() for h in (header_names or []) if str(h).strip()]
    if not names:
        return
    header_schema = {
        "headers": [
            {"name": name, "type": "string", "position": idx}
            for idx, name in enumerate(names)
        ]
    }
    ai_categorization = {
        "document_type": "Pending",
        "document_description": f"Workbook: {spreadsheet_name}",
    }
    existing = await database.fetch_one(
        """
        SELECT id FROM gsheets_extracted_schemas
        WHERE user_id = CAST(:user_id AS uuid)
          AND spreadsheet_id = :spreadsheet_id
          AND sheet_name = :sheet_name
        """,
        {
            "user_id": str(user_id),
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
        },
    )
    payload = {
        "header_schema": json.dumps(header_schema),
        "ai_categorization": json.dumps(ai_categorization),
        "spreadsheet_name": spreadsheet_name,
    }
    if existing:
        row_id = existing["id"] if isinstance(existing, dict) else existing[0]
        await database.execute(
            """
            UPDATE gsheets_extracted_schemas
            SET header_schema = :header_schema,
                ai_categorization = :ai_categorization,
                spreadsheet_name = :spreadsheet_name,
                extracted_at = NOW()
            WHERE id = :id
            """,
            {**payload, "id": row_id},
        )
        return
    await database.execute(
        """
        INSERT INTO gsheets_extracted_schemas
        (id, user_id, google_sheets_connection_id, spreadsheet_id, spreadsheet_name,
         sheet_name, header_schema, ai_categorization, extracted_at)
        VALUES (:id, :user_id, :conn_id, :spreadsheet_id, :spreadsheet_name,
                :sheet_name, :header_schema, :ai_categorization, NOW())
        """,
        {
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "conn_id": google_sheets_connection_id,
            "spreadsheet_id": spreadsheet_id,
            "spreadsheet_name": spreadsheet_name,
            "sheet_name": sheet_name,
            **payload,
        },
    )


async def store_google_spreadsheet_metadata(
    user_id: str,
    spreadsheet_id: str,
    spreadsheet_name: str,
    sheet_name: str,
    sheet_id: str = None,
    google_sheets_connection_id: str = None
) -> bool:
    """
    Store or update Google Spreadsheet metadata in the database.
    Returns True if successful, False otherwise.
    """
    try:
        from db.connection import database
        
        # Check if this spreadsheet already exists for this user and connection
        # Handle NULL sheet_name properly using COALESCE or IS NULL comparison
        existing = await database.fetch_one(
            """
            SELECT id, access_count FROM user_google_spreadsheets 
            WHERE user_id = :user_id 
              AND google_sheets_connection_id = :connection_id 
              AND spreadsheet_id = :spreadsheet_id 
              AND (sheet_name = :sheet_name OR (sheet_name IS NULL AND :sheet_name IS NULL))
            """,
            {
                "user_id": user_id,
                "connection_id": google_sheets_connection_id,
                "spreadsheet_id": spreadsheet_id,
                "sheet_name": sheet_name
            }
        )
        
        if existing:
            # Update existing record - increment access count and update timestamp
            await database.execute(
                """
                UPDATE user_google_spreadsheets 
                SET access_count = access_count + 1,
                    last_accessed_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id
                """,
                {"id": existing["id"]}
            )
        else:
            # Insert new record
            await database.execute(
                """
                INSERT INTO user_google_spreadsheets 
                (user_id, google_sheets_connection_id, spreadsheet_id, spreadsheet_name, sheet_name, sheet_id, created_at, updated_at)
                VALUES (:user_id, :connection_id, :spreadsheet_id, :spreadsheet_name, :sheet_name, :sheet_id, NOW(), NOW())
                """,
                {
                    "user_id": user_id,
                    "connection_id": google_sheets_connection_id,
                    "spreadsheet_id": spreadsheet_id,
                    "spreadsheet_name": spreadsheet_name,
                    "sheet_name": sheet_name,
                    "sheet_id": sheet_id
                }
            )
        
        return True
        
    except Exception as e:
        print(f"❌ Error storing Google Spreadsheet metadata: {e}")
        return False


async def update_google_spreadsheet_metadata(
    user_id: str,
    spreadsheet_id: str,
    new_title: str,
    google_sheets_connection_id: str = None
) -> Dict[str, Any]:
    """
    Update spreadsheet metadata (title) in both Google Sheets and database
    
    Args:
        user_id: User ID
        spreadsheet_id: The ID of the spreadsheet to update
        new_title: New title/name for the spreadsheet
        google_sheets_connection_id: Optional connection ID (will be fetched if not provided)
    
    Returns:
        Dictionary with operation result
    """
    try:
        from db.connection import database
        from services.google_sheets_service import GoogleSheetsService
        
        # Get valid Google Sheets token (refreshes if expired)
        from utils.google_sheets_auth import get_valid_google_sheets_token
        
        google_sheets_connection = await get_valid_google_sheets_token(
            user_id,
            google_sheets_connection_id
        )
        
        if not google_sheets_connection:
            return {
                'success': False,
                'error': 'Google Sheets connection not found. Please connect your Google account first.'
            }
        
        # Initialize Google Sheets Service
        sheets_service = GoogleSheetsService(
            access_token=google_sheets_connection['access_token'],
            refresh_token=google_sheets_connection['refresh_token'],
            expires_at=google_sheets_connection['expires_at']
        )
        
        # Update spreadsheet metadata in Google Sheets
        update_result = await sheets_service.update_spreadsheet_metadata(
            spreadsheet_id=spreadsheet_id,
            title=new_title
        )
        
        if not update_result.get('success'):
            return update_result
        
        # Update database records for all sheets of this spreadsheet
        await database.execute(
            """
            UPDATE gsheets_extracted_schemas 
            SET spreadsheet_name = :new_title
            WHERE user_id = :user_id 
              AND spreadsheet_id = :spreadsheet_id
            """,
            {
                "user_id": user_id,
                "spreadsheet_id": spreadsheet_id,
                "new_title": new_title
            }
        )
        await database.execute(
            """
            UPDATE user_google_spreadsheets 
            SET spreadsheet_name = :new_title, updated_at = NOW()
            WHERE user_id = :user_id 
              AND spreadsheet_id = :spreadsheet_id
            """,
            {
                "user_id": user_id,
                "spreadsheet_id": spreadsheet_id,
                "new_title": new_title
            }
        )
        
        print(f"✅ Updated spreadsheet metadata in database: {spreadsheet_id} -> {new_title}")
        
        return {
            'success': True,
            'spreadsheet_id': spreadsheet_id,
            'spreadsheet_name': new_title,
            'spreadsheet_url': update_result.get('spreadsheet_url'),
            'message': f'Spreadsheet renamed to "{new_title}" successfully'
        }
        
    except Exception as e:
        print(f"❌ Error updating Google Spreadsheet metadata: {e}")
        import traceback
        traceback.print_exc()
        return {
            'success': False,
            'error': f'Failed to update spreadsheet metadata: {str(e)}'
        }


