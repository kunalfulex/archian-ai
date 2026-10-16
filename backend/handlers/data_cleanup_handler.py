"""
Data Cleanup Handler Module

This module handles data cleanup requests by:
- Extracting job_id and data_quality_report from conversation_history
- Converting quality_report to extraction_rules format
- Calling existing extraction flow with converted rules and parent_job_id
"""

import json
import uuid
from typing import Dict, Any, Optional, List

from utils.data_quality_analyzer import convert_quality_report_to_extraction_rules


async def handle_data_cleanup_stream(
    prompt: str,
    user_id: str,
    conversation_history: List[Dict],
    conversation_id: Optional[str],
    source: str,
    source_name: Optional[str],
    destination: str,
    destination_name: Optional[str]
):
    """
    Handle data cleanup requests by:
    1. Extracting job_id and data_quality_report from conversation_history
    2. Converting quality_report to extraction_rules
    3. Calling existing extraction flow with parent_job_id
    
    Args:
        prompt: User's cleanup request
        user_id: User ID
        conversation_history: Previous conversation messages
        conversation_id: Conversation ID
        source: Source type (from classification)
        source_name: Source name (from classification)
        destination: Destination type (from classification)
        destination_name: Destination name (from classification)
    
    Returns:
        Streaming response with job_id (for consistency with other handlers)
    """
    try:
        # Step 1: Find extraction_completed event with data_quality_report
        extraction_event = None
        original_job_id = None
        data_quality_report = None
        intent_state = None
        
        # Extract all metadata fields from extraction_completed event
        original_extraction_rules = None
        document_type = None
        document_description = None
        mapping_context_summary = None
        connector_id = None
        
        for msg in reversed(conversation_history):
            metadata = msg.get('metadata', {})
            if not isinstance(metadata, dict):
                continue
            
            event_type = metadata.get('event_type')
            if event_type == 'extraction_completed':
                # Check if it has data_quality_report
                # Note: In chat_service.log_data_event, event_data fields are spread directly into metadata
                # So extraction_rules, job_id, data_quality_report are at metadata level, not nested
                quality_report = metadata.get('data_quality_report')
                # Require a present report; accept either has_issues or any non-empty report (see prior OR logic).
                # Guard: never call .get on None — `None or x.get` still evaluates x.get and crashes.
                if (quality_report and quality_report.get('summary', {}).get('has_issues')) or quality_report:
                    extraction_event = msg
                    original_job_id = metadata.get('job_id')
                    data_quality_report = quality_report
                    intent_state = metadata.get('intent_state', {})
                    
                    # Extract all metadata fields (event_data fields are spread directly into metadata)
                    original_extraction_rules = metadata.get('extraction_rules')
                    document_type = metadata.get('document_type')
                    document_description = metadata.get('document_description')
                    mapping_context_summary = metadata.get('mapping_context_summary')
                    connector_id = metadata.get('connector_id')
                    
                    # Validate extraction_rules type if present
                    if original_extraction_rules:
                        if isinstance(original_extraction_rules, list):
                            print(f"⚠️ [CLEANUP] extraction_rules is a list, expected dict. Setting to None.")
                            original_extraction_rules = None
                        elif not isinstance(original_extraction_rules, dict):
                            print(f"⚠️ [CLEANUP] extraction_rules is unexpected type: {type(original_extraction_rules)}, setting to None")
                            original_extraction_rules = None
                    break
        
        if not extraction_event or not original_job_id or not data_quality_report:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find extraction with data quality report. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Step 2: Override destination/destination_name from intent_state (source of truth)
        # This ensures cleanup continues the previous extraction flow
        original_destination = destination
        original_destination_name = destination_name
        destination_was_overridden = False  # Track if destination was overridden (needed for document_type/document_description update)
        
        if intent_state and isinstance(intent_state, dict):
            # Check if user explicitly mentioned a different destination in cleanup prompt
            prompt_lower = prompt.lower()
            mentions_different_destination = (
                "migrate to" in prompt_lower or 
                ("to " in prompt_lower and any(keyword in prompt_lower for keyword in ["sheet", "spreadsheet", "salesforce", "org", "database", "db"]))
            )
            
            if not mentions_different_destination:
                # Use intent_state values (continuation of previous extraction)
                intent_destination = intent_state.get("active_destination")
                intent_destination_name = intent_state.get("destination_name")
                
                if intent_destination:
                    destination = intent_destination
                if intent_destination_name:
                    destination_name = intent_destination_name
                # destination_was_overridden remains False - keep original document_type/document_description
            else:
                # User explicitly mentioned different destination, keep classification values
                destination_was_overridden = True  # Mark that destination changed
        else:
            # If no intent_state, assume destination might be new (could be first cleanup or destination changed)
            destination_was_overridden = True

        # Cleanup is free; no credit deduction

        # Step 3: Convert quality_report to extraction_rules (cleanup rules only)
        cleanup_extraction_rules = convert_quality_report_to_extraction_rules(data_quality_report)
        
        # Step 4: Route to appropriate extraction handler based on source
        # Pass both original_extraction_rules (from initial extraction) and cleanup_extraction_rules (from quality report)
        if source == "file":
            return await _handle_file_cleanup_stream(
                original_job_id=original_job_id,
                extraction_rules=cleanup_extraction_rules,  # Cleanup rules from quality report
                original_extraction_rules=original_extraction_rules,  # Original rules from initial extraction
                user_id=user_id,
                destination=destination,
                destination_name=destination_name,
                conversation_id=conversation_id,
                intent_state=intent_state,
                cleanup_prompt=prompt,  # Pass user's cleanup prompt
                destination_was_overridden=destination_was_overridden
            )
        elif source == "google_sheets":
            return await _handle_google_sheets_cleanup_stream(
                original_job_id=original_job_id,
                extraction_rules=cleanup_extraction_rules,  # Cleanup rules from quality report
                original_extraction_rules=original_extraction_rules,  # Original rules from initial extraction
                user_id=user_id,
                destination=destination,
                destination_name=destination_name,
                conversation_id=conversation_id,
                intent_state=intent_state,
                cleanup_prompt=prompt,  # Pass user's cleanup prompt
                destination_was_overridden=destination_was_overridden
            )
        elif source == "sql":
            return await _handle_sql_cleanup_stream(
                original_job_id=original_job_id,
                extraction_rules=cleanup_extraction_rules,  # Cleanup rules from quality report
                original_extraction_rules=original_extraction_rules,  # Original rules from initial extraction
                user_id=user_id,
                destination=destination,
                destination_name=destination_name,
                conversation_id=conversation_id,
                intent_state=intent_state,
                cleanup_prompt=prompt,  # Pass user's cleanup prompt
                destination_was_overridden=destination_was_overridden,
                document_type=document_type,  # From metadata
                document_description=document_description,  # From metadata
                mapping_context_summary=mapping_context_summary,  # From metadata
                connector_id=connector_id  # From metadata
            )
        elif source == "salesforce":
            return await _handle_salesforce_cleanup_stream(
                original_job_id=original_job_id,
                extraction_rules=cleanup_extraction_rules,  # Cleanup rules from quality report
                original_extraction_rules=original_extraction_rules,  # Original rules from initial extraction
                user_id=user_id,
                destination=destination,
                destination_name=destination_name,
                conversation_id=conversation_id,
                intent_state=intent_state,
                cleanup_prompt=prompt,  # Pass user's cleanup prompt
                destination_was_overridden=destination_was_overridden
            )
        else:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': f'Cleanup for source type {source} is not supported'})}\n\n"
            return error_generator()
        
    except Exception as e:
        print(f"❌ Error in data cleanup handler: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)  # Capture error message in outer scope
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start data cleanup: {error_message}'})}\n\n"
        return error_generator()


async def _handle_file_cleanup_stream(
    original_job_id: str,
    extraction_rules: Dict[str, Any],  # Cleanup rules from quality report
    original_extraction_rules: Optional[Dict[str, Any]],  # Original extraction rules from initial extraction
    user_id: str,
    destination: str,
    destination_name: Optional[str],
    conversation_id: Optional[str],
    intent_state: Dict[str, Any],
    cleanup_prompt: str,
    destination_was_overridden: bool = False
):
    """
    Handle file cleanup by calling process_extraction_background with extraction_rules.
    """
    try:
        # Get file_key from the original extraction result
        from services.extraction_results_service import ExtractionResultsService
        extraction_result = await ExtractionResultsService.get_extraction_result(
            original_job_id, user_id
        )
        
        if not extraction_result:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction data. Please re-upload the file.'})}\n\n"
            return error_generator()
        
        # Get extracted_data from extraction_result
        extracted_data = extraction_result.get("extracted_data", [])
        if not extracted_data:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find extracted data for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get mapping_context_summary from extraction_completed message metadata
        mapping_context_summary = None
        
        # Try to get from extraction_completed message metadata first
        from utils.job_stores import extraction_jobs
        original_job = await extraction_jobs.get(original_job_id, {})
        extraction_message_id = original_job.get("result", {}).get("extraction_message_id")
        if extraction_message_id and conversation_id:
            try:
                from db.connection import database
                message_record = await database.fetch_one(
                    """
                    SELECT metadata
                    FROM chat_messages
                    WHERE id = :message_id AND conversation_id = :conversation_id AND user_id = :user_id
                    LIMIT 1
                    """,
                    {
                        "message_id": extraction_message_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id
                    }
                )
                # Convert Record to dict for safe access
                if message_record:
                    message_record_dict = dict(message_record) if hasattr(message_record, '_mapping') or hasattr(message_record, 'keys') else message_record
                    metadata = message_record_dict.get("metadata")
                    if metadata:
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata)
                            event_data = metadata.get("event_data", {}) if isinstance(metadata, dict) else {}
                            mapping_context_summary = event_data.get("mapping_context_summary")
            except Exception as e:
                print(f"⚠️ Warning: Could not retrieve message metadata: {e}")
        
        # Get file_key from source_identifier (for files, this is the file_key)
        file_key = extraction_result.get("source_identifier") or extraction_result.get("source_name")
        if not file_key:
            # Try to get from job status as fallback
            try:
                from services.flow_migration_background import get_background_job
                job_status, _ = await get_background_job(original_job_id)
                if job_status and job_status.get("result"):
                    file_key = job_status["result"].get("file_key")
            except:
                pass
        
        if not file_key:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find file information for cleanup. Please re-upload the file.'})}\n\n"
            return error_generator()
        
        # Create new job_id for version 2
        new_job_id = str(uuid.uuid4())
        
        # Extract file extension from file_key
        import os
        file_extension = os.path.splitext(file_key)[1].lower()
        
        # Use user's cleanup prompt as extraction_goals (or fallback to default)
        extraction_goals = cleanup_prompt if cleanup_prompt else "Clean data: remove duplicates, fix case inconsistencies, and format issues"
        
        # Start background job - store extraction_rules AND extracted_data in job metadata
        # process_extraction_background will use existing data instead of re-extracting
        import asyncio
        from services.file_extraction.background import process_extraction_background
        from utils.job_stores import extraction_jobs
        
        # Store extraction_rules in job metadata (extracted_data will be passed as parameter)
        # Note: document_type and document_description will be updated later if destination was overridden
        file_document_type = extraction_result.get("document_type")
        file_document_description = extraction_result.get("document_description")
        await extraction_jobs.set(new_job_id, {
            "status": "pending",
            "cleanup_extraction_rules": extraction_rules,  # Store cleanup rules from quality report
            "original_extraction_rules": original_extraction_rules,  # Store original extraction rules from initial extraction
            "intent": "cleanup",
            "parent_job_id": original_job_id,
            "source_type": extraction_result.get("source_type", "file"),
            "source_name": extraction_result.get("source_name"),
            "source_identifier": extraction_result.get("source_identifier"),
            "document_type": file_document_type,  # Will be updated if destination was overridden
            "document_description": file_document_description,  # Will be updated if destination was overridden
            "mapping_context_summary": mapping_context_summary,  # Preserve mapping_context_summary from message metadata
            "destination_was_overridden": destination_was_overridden,  # Track if destination changed (for document_type/document_description update)
        })
        
        asyncio.create_task(process_extraction_background(
            job_id=new_job_id,
            file_key=file_key,
            file_extension=file_extension,
            extraction_goals=extraction_goals,
            user_id=user_id,
            flow_id=None,
            classification=None,
            source_type="file",
            destination=destination,
            destination_name=destination_name,
            intent="cleanup",
            conversation_id=conversation_id,
            parent_job_id=original_job_id,
            extracted_data=extracted_data,  # Pass processed data from extraction_results table
        ))
        async def stream_generator():
            yield f"data: {json.dumps({'status': 'processing', 'source_type': 'file', 'job_id': new_job_id, 'message': 'Data cleanup started in background. Processing...'})}\n\n"
            from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal

            async for evt in stream_extraction_job_events_until_terminal(new_job_id, extraction_jobs):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return stream_generator()
        
    except Exception as e:
        print(f"❌ Error starting file cleanup: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start data cleanup: {error_message}'})}\n\n"
        return error_generator()


async def _handle_google_sheets_cleanup_stream(
    original_job_id: str,
    extraction_rules: Dict[str, Any],  # Cleanup rules from quality report
    original_extraction_rules: Optional[Dict[str, Any]],  # Original extraction rules from initial extraction
    user_id: str,
    destination: str,
    destination_name: Optional[str],
    conversation_id: Optional[str],
    intent_state: Dict[str, Any],
    cleanup_prompt: str,
    destination_was_overridden: bool = False
):
    """
    Handle Google Sheets cleanup by calling process_google_sheets_extraction_background with extraction_rules.
    """
    try:
        # Get original job data from extraction_jobs
        from utils.job_stores import extraction_jobs
        original_job = await extraction_jobs.get(original_job_id, {})
        
        if not original_job:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction job. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get extracted_data from extraction_results table
        from services.extraction_results_service import ExtractionResultsService
        extraction_result = await ExtractionResultsService.get_extraction_result(
            original_job_id, user_id
        )
        
        if not extraction_result:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction data. Please run extraction first.'})}\n\n"
            return error_generator()
        
        extracted_data = extraction_result.get("extracted_data", [])
        if not extracted_data:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find extracted data for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get document_type, document_description, connector_id, and mapping_context_summary from extraction_completed message metadata
        # Fallback to extraction_results table if message metadata is not available
        document_type = None
        document_description = None
        connector_id = None
        mapping_context_summary = None
        
        # Try to get from extraction_completed message metadata first
        extraction_message_id = original_job.get("result", {}).get("extraction_message_id")
        if extraction_message_id and conversation_id:
            try:
                from db.connection import database
                message_record = await database.fetch_one(
                    """
                    SELECT metadata
                    FROM chat_messages
                    WHERE id = :message_id AND conversation_id = :conversation_id AND user_id = :user_id
                    LIMIT 1
                    """,
                    {
                        "message_id": extraction_message_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id
                    }
                )
                # Convert Record to dict for safe access
                if message_record:
                    message_record_dict = dict(message_record) if hasattr(message_record, '_mapping') or hasattr(message_record, 'keys') else message_record
                    metadata = message_record_dict.get("metadata")
                    if metadata:
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata)
                            event_data = metadata.get("event_data", {}) if isinstance(metadata, dict) else {}
                            document_type = event_data.get("document_type")
                            document_description = event_data.get("document_description")
                            connector_id = event_data.get("connector_id")
                            mapping_context_summary = event_data.get("mapping_context_summary")
            except Exception as e:
                print(f"⚠️ Warning: Could not retrieve message metadata: {e}")
        
        # Fallback to extraction_results table for document_type and document_description
        # BUT: If destination was overridden, skip using old values (they're for old destination)
        # New values will be generated later from cleanup prompt in process_google_sheets_extraction_background
        if not destination_was_overridden:
            if not document_type:
                document_type = extraction_result.get("document_type")
            if not document_description:
                document_description = extraction_result.get("document_description")
        
        # Extract required information from original job
        spreadsheet_name = original_job.get("spreadsheet_name")
        sheet_name = original_job.get("sheet_name")
        
        if not spreadsheet_name:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find spreadsheet information for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get spreadsheet_id and google_sheets_connection_id from database
        # Use connector_id from message metadata if available, otherwise fetch from database
        from db.connection import database
        if connector_id:
            # Use connector_id from message metadata
            spreadsheet_record = await database.fetch_one(
                """
                SELECT 
                    ugs.spreadsheet_id,
                    ugs.google_sheets_connection_id,
                    ugs.sheet_name
                FROM user_google_spreadsheets ugs
                WHERE ugs.user_id = :user_id 
                  AND ugs.spreadsheet_name = :spreadsheet_name
                  AND ugs.id = :connector_id
                ORDER BY ugs.last_accessed_at DESC
                LIMIT 1
                """,
                {
                    "user_id": user_id,
                    "spreadsheet_name": spreadsheet_name,
                    "connector_id": connector_id
                }
            )
        else:
            # Fallback: fetch without connector_id filter
            spreadsheet_record = await database.fetch_one(
                """
                SELECT 
                    ugs.spreadsheet_id,
                    ugs.google_sheets_connection_id,
                    ugs.sheet_name
                FROM user_google_spreadsheets ugs
                WHERE ugs.user_id = :user_id 
                  AND ugs.spreadsheet_name = :spreadsheet_name
                ORDER BY ugs.last_accessed_at DESC
                LIMIT 1
                """,
                {
                    "user_id": user_id,
                    "spreadsheet_name": spreadsheet_name
                }
            )
        
        if not spreadsheet_record:
            async def error_generator():
                error_message = f'Could not find spreadsheet "{spreadsheet_name}" in your accessible spreadsheets.'
                yield f"data: {json.dumps({'status': 'error', 'message': error_message})}\n\n"
            return error_generator()
        
        spreadsheet_id = spreadsheet_record["spreadsheet_id"]
        google_sheets_connection_id = spreadsheet_record["google_sheets_connection_id"]
        
        # Use sheet_name from job or database record
        if not sheet_name:
            sheet_name = spreadsheet_record.get("sheet_name") or "Sheet1"
        
        # Default range_name
        range_name = "A1:ZZ"
        
        # Create new job_id for version 2
        new_job_id = str(uuid.uuid4())
        
        # Use user's cleanup prompt as extraction_goals (or fallback to default)
        extraction_goals = cleanup_prompt if cleanup_prompt else "Clean data: remove duplicates, fix case inconsistencies, and format issues"
        
        # Store extraction_rules in job metadata (extracted_data will be passed as parameter)
        # Note: document_type and document_description will be updated later if destination was overridden
        cleanup_job_data = {
            "status": "pending",
            "cleanup_extraction_rules": extraction_rules,  # Store cleanup rules from quality report
            "original_extraction_rules": original_extraction_rules,  # Store original extraction rules from initial extraction
            "intent": "cleanup",
            "parent_job_id": original_job_id,
            "source_type": extraction_result.get("source_type", "google_sheets"),
            "source_name": extraction_result.get("source_name"),
            "document_type": document_type,  # Will be updated if destination was overridden
            "document_description": document_description,  # Will be updated if destination was overridden
            "connector_id": connector_id,  # Preserve connector_id from message metadata
            "mapping_context_summary": mapping_context_summary,  # Preserve mapping_context_summary from message metadata
            "destination_was_overridden": destination_was_overridden,  # Track if destination changed (for document_type/document_description update)
        }
        await extraction_jobs.set(new_job_id, cleanup_job_data)
        
        # Start background job
        import asyncio
        from handlers.google_sheets_handler import process_google_sheets_extraction_background
        
        asyncio.create_task(process_google_sheets_extraction_background(
            job_id=new_job_id,
            spreadsheet_id=spreadsheet_id,
            spreadsheet_name=spreadsheet_name,
            sheet_name=sheet_name,
            range_name=range_name,
            extraction_goals=extraction_goals,
            user_id=user_id,
            google_sheets_connection_id=google_sheets_connection_id,
            destination=destination,
            destination_name=destination_name,
            conversation_id=conversation_id,
            parent_job_id=original_job_id,
            extraction_rules=extraction_rules,  # Pass cleanup rules
            extracted_data=extracted_data  # Pass processed data from extraction_results table
        ))
        
        async def stream_generator():
            yield f"data: {json.dumps({'status': 'processing', 'source_type': 'google_sheets', 'job_id': new_job_id, 'message': 'Data cleanup started in background. Processing...'})}\n\n"
            from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal

            async for evt in stream_extraction_job_events_until_terminal(new_job_id, extraction_jobs):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return stream_generator()
        
    except Exception as e:
        print(f"❌ Error starting Google Sheets cleanup: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start data cleanup: {error_message}'})}\n\n"
        return error_generator()


async def _handle_sql_cleanup_stream(
    original_job_id: str,
    extraction_rules: Dict[str, Any],  # Cleanup rules from quality report
    original_extraction_rules: Optional[Dict[str, Any]],  # Original extraction rules from initial extraction
    user_id: str,
    destination: str,
    destination_name: Optional[str],
    conversation_id: Optional[str],
    intent_state: Dict[str, Any],
    cleanup_prompt: str,
    destination_was_overridden: bool = False,
    document_type: Optional[str] = None,  # From metadata
    document_description: Optional[str] = None,  # From metadata
    mapping_context_summary: Optional[str] = None,  # From metadata
    connector_id: Optional[str] = None  # From metadata
):
    """
    Handle SQL cleanup by calling start_sql_extraction_task with extraction_rules.
    """
    try:
        # Get original task data from sql_tasks / Redis (needed for connector_data, table_and_query)
        from utils.job_stores import sql_tasks
        original_task = await sql_tasks.get(original_job_id)
        
        if not original_task:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction task. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get extracted_data from extraction_results table
        from services.extraction_results_service import ExtractionResultsService
        extraction_result = await ExtractionResultsService.get_extraction_result(
            original_job_id, user_id
        )
        
        if not extraction_result:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction data. Please run extraction first.'})}\n\n"
            return error_generator()
        
        extracted_data = extraction_result.get("extracted_data", [])
        if not extracted_data:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find extracted data for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Fallback to extraction_results table for document_type and document_description if not provided
        # BUT: If destination was overridden, skip using old values (they're for old destination)
        # New values will be generated later from cleanup prompt in _process_sql_extraction
        if not destination_was_overridden:
            if not document_type:
                document_type = extraction_result.get("document_type")
            if not document_description:
                document_description = extraction_result.get("document_description")
        
        # Extract required information from original task
        connector_data = original_task.get("connector_data")
        table_and_query = original_task.get("table_and_query")
        comprehensive_prompt = original_task.get("comprehensive_prompt")
        
        if not connector_data or not table_and_query:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find SQL connection or query information for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Use user's cleanup prompt (or fallback to default)
        cleanup_prompt_value = cleanup_prompt if cleanup_prompt else "Clean data: remove duplicates, fix case inconsistencies, and format issues"
        
        # Create new job_id for cleanup (matching Google Sheets pattern)
        import uuid
        new_job_id = str(uuid.uuid4())
        
        # Store all task data with metadata BEFORE starting background task (matching Google Sheets pattern)
        # This prevents race condition where background task reads incomplete data
        from utils.job_stores import sql_tasks
        from services.sql_background_processing_service import _sanitize_for_redis
        from datetime import datetime
        
        cleanup_task_data = {
            "job_id": new_job_id,
            "user_id": user_id,
            "connector_data": connector_data,
            "table_and_query": table_and_query,
            "comprehensive_prompt": cleanup_prompt_value,
            "destination": destination,
            "destination_name": destination_name,
            "conversation_id": conversation_id,
            "parent_job_id": original_job_id,
            "extraction_rules": extraction_rules,  # Store cleanup rules from quality report
            "extracted_data": extracted_data,  # Store processed data from extraction_results table (version 1)
            "intent": "cleanup",  # Set intent for cleanup
            "cleanup_extraction_rules": extraction_rules,  # Store cleanup rules from quality report
            "original_extraction_rules": original_extraction_rules,  # Store original extraction rules from initial extraction
            "document_type": document_type,  # Will be updated if destination was overridden
            "document_description": document_description,  # Will be updated if destination was overridden
            "connector_id": connector_id,  # Preserve connector_id from message metadata
            "mapping_context_summary": mapping_context_summary,  # Preserve mapping_context_summary from message metadata
            "destination_was_overridden": destination_was_overridden,  # Track if destination changed (for document_type/document_description update)
            "status": "pending",
            "progress": 0,
            "message": "Starting SQL extraction...",
            "result": None,
            "error": None,
            "created_at": datetime.now().isoformat()
        }
        
        # Store task data in Redis BEFORE starting background task
        await sql_tasks.set(new_job_id, _sanitize_for_redis(cleanup_task_data))
        
        # Start background task (matching Google Sheets pattern)
        import asyncio
        from services.sql_background_processing_service import _process_sql_extraction
        
        asyncio.create_task(_process_sql_extraction(new_job_id))
        
        async def stream_generator():
            yield f"data: {json.dumps({'status': 'processing', 'source_type': 'sql', 'job_id': new_job_id, 'message': 'Data cleanup started in background. Processing...'})}\n\n"
            from utils.extraction_sse_stream import stream_sql_job_events_until_terminal

            async for evt in stream_sql_job_events_until_terminal(new_job_id, sql_tasks):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return stream_generator()
        
    except Exception as e:
        print(f"❌ Error starting SQL cleanup: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start data cleanup: {error_message}'})}\n\n"
        return error_generator()


async def _handle_salesforce_cleanup_stream(
    original_job_id: str,
    extraction_rules: Dict[str, Any],  # Cleanup rules from quality report
    original_extraction_rules: Optional[Dict[str, Any]],  # Original extraction rules from initial extraction
    user_id: str,
    destination: str,
    destination_name: Optional[str],
    conversation_id: Optional[str],
    intent_state: Dict[str, Any],
    cleanup_prompt: str,
    destination_was_overridden: bool = False
):
    """
    Handle Salesforce cleanup by calling start_salesforce_extraction_job with extraction_rules.
    """
    try:
        # Get original job data from extraction_jobs
        from utils.job_stores import extraction_jobs
        original_job = await extraction_jobs.get(original_job_id, {})
        
        if not original_job:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction job. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get extracted_data from extraction_results table
        from services.extraction_results_service import ExtractionResultsService
        extraction_result = await ExtractionResultsService.get_extraction_result(
            original_job_id, user_id
        )
        
        if not extraction_result:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction data. Please run extraction first.'})}\n\n"
            return error_generator()
        
        extracted_data = extraction_result.get("extracted_data", [])
        if not extracted_data:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find extracted data for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Extract required information from original job
        org_info = original_job.get("org_info") or {
            "org_id": original_job.get("org_id"),
            "connection_name": original_job.get("connection_name"),
            "environment_type": original_job.get("environment_type"),
            "connected_app_id": original_job.get("connected_app_id"),
        }
        object_result = original_job.get("object_result") or {
            "object_api_name": original_job.get("object_api_name"),
            "object_label": original_job.get("object_label"),
            "selected_fields": original_job.get("fields"),
        }
        soql_payload = original_job.get("soql_payload") or {
            "soql": original_job.get("soql"),
            "used_fields": original_job.get("fields"),
            "filter_summary": original_job.get("filter_summary"),
        }
        
        if not org_info.get("org_id") or not object_result.get("object_api_name"):
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find Salesforce org or object information for cleanup. Please run extraction first.'})}\n\n"
            return error_generator()
        
        # Get document_type, document_description, and mapping_context_summary from extraction_completed message metadata
        # Fallback to extraction_results table if message metadata is not available
        document_type = None
        document_description = None
        mapping_context_summary = None
        
        # Try to get from extraction_completed message metadata first
        extraction_message_id = original_job.get("result", {}).get("extraction_message_id")
        if extraction_message_id and conversation_id:
            try:
                from db.connection import database
                message_record = await database.fetch_one(
                    """
                    SELECT metadata
                    FROM chat_messages
                    WHERE id = :message_id AND conversation_id = :conversation_id AND user_id = :user_id
                    LIMIT 1
                    """,
                    {
                        "message_id": extraction_message_id,
                        "conversation_id": conversation_id,
                        "user_id": user_id
                    }
                )
                # Convert Record to dict for safe access
                if message_record:
                    message_record_dict = dict(message_record) if hasattr(message_record, '_mapping') or hasattr(message_record, 'keys') else message_record
                    metadata = message_record_dict.get("metadata")
                    if metadata:
                        if isinstance(metadata, str):
                            metadata = json.loads(metadata)
                        event_data = metadata.get("event_data", {}) if isinstance(metadata, dict) else {}
                        document_type = event_data.get("document_type")
                        document_description = event_data.get("document_description")
                        mapping_context_summary = event_data.get("mapping_context_summary")
            except Exception as e:
                print(f"⚠️ Warning: Could not retrieve message metadata: {e}")
        
        # Fallback to extraction_results table for document_type and document_description
        # BUT: If destination was overridden, skip using old values (they're for old destination)
        # New values will be generated later from cleanup prompt in _process_salesforce_extraction_job
        if not destination_was_overridden:
            if not document_type:
                document_type = extraction_result.get("document_type")
        if not document_description:
            document_description = extraction_result.get("document_description")
        
        # Use cleanup prompt (or fallback to default)
        cleanup_prompt_value = cleanup_prompt if cleanup_prompt else "Clean data: remove duplicates, fix case inconsistencies, and format issues"
        
        # Store quality report rules separately - they will be merged with prompt-generated rules
        # after extract_structured_data_from_salesforce generates rules from the cleanup prompt
        quality_report_rules = extraction_rules.copy() if extraction_rules else {}
        
        # Start new Salesforce extraction job - don't pass extraction_rules
        # Let extract_structured_data_from_salesforce generate rules from cleanup prompt
        # Quality report rules will be merged afterward
        from handlers.salesforce_handler import start_salesforce_extraction_job

        new_job_id = await start_salesforce_extraction_job(
            user_id=user_id,
            org_info=org_info,
            object_result=object_result,
            soql_payload=soql_payload,
            destination=destination,
            destination_name=destination_name,
            extraction_prompt=cleanup_prompt_value,
            conversation_id=conversation_id,
            parent_job_id=original_job_id,
            extraction_rules=None,  # Let extract_structured_data_from_salesforce generate rules from prompt
            extracted_data=extracted_data  # Pass processed data from extraction_results table
        )
        
        # Store cleanup intent and quality report rules in job metadata
        # These will be merged with prompt-generated rules after extraction
        await extraction_jobs.update(new_job_id, {
            "intent": "cleanup",
            "cleanup_extraction_rules": quality_report_rules,  # Store cleanup rules from quality report
            "original_extraction_rules": original_extraction_rules,  # Store original extraction rules from initial extraction
            "document_type": document_type,  # Will be updated if destination was overridden
            "document_description": document_description,  # Will be updated if destination was overridden
            "mapping_context_summary": mapping_context_summary,  # Preserve mapping_context_summary from message metadata
            "destination_was_overridden": destination_was_overridden,  # Track if destination changed (for document_type/document_description update)
        })
        
        async def stream_generator():
            yield f"data: {json.dumps({'status': 'processing', 'source_type': 'salesforce', 'job_id': new_job_id, 'message': 'Data cleanup started in background. Processing...'})}\n\n"
            from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal

            async for evt in stream_extraction_job_events_until_terminal(new_job_id, extraction_jobs):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return stream_generator()
        
    except Exception as e:
        print(f"❌ Error starting Salesforce cleanup: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start data cleanup: {error_message}'})}\n\n"
        return error_generator()
