"""
File Re-extraction Handler Module

This module handles re-extraction requests for files by:
- Retrieving stored extraction data from extraction_results table
- Starting a new extraction job using the same process_extraction_background function
"""

import json
import uuid
from typing import Dict, Any

from services.extraction_results_service import ExtractionResultsService


async def handle_file_reextraction_stream(
    prompt: str,
    user_id: str,
    conversation_history: list,
    intent_state: Dict[str, Any],
    conversation_id: str,
    destination: str,
    destination_name: str
):
    """
    Handle re-extraction requests for files by starting a background job.
    Uses the same process_extraction_background function as first-time extraction.
    Returns processing chunk then holds the stream open with extraction_progress / extraction_job_finished (fallback: /job-status).
    
    Args:
        prompt: User's modification request (new extraction goals)
        user_id: User ID
        conversation_history: Previous conversation messages
        intent_state: Active intent state from extraction_completed event
        conversation_id: Conversation ID
    
    Returns:
        Streaming response with job_id (for consistency with other handlers)
    """
    try:
        # Get job_id from intent_state
        original_job_id = intent_state.get("job_id")
        if not original_job_id:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction job ID'})}\n\n"
            return error_generator()
        
        # Get file_key from the original extraction result
        extraction_result = await ExtractionResultsService.get_extraction_result(
            original_job_id, user_id
        )
        
        if not extraction_result:
            async def error_generator():
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find original extraction data. Please re-upload the file.'})}\n\n"
            return error_generator()
        
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
                yield f"data: {json.dumps({'status': 'error', 'message': 'Could not find file information for re-extraction. Please re-upload the file.'})}\n\n"
            return error_generator()
        
        # Create new job_id for version 2
        new_job_id = str(uuid.uuid4())
        
        # Extract file extension from file_key
        import os
        file_extension = os.path.splitext(file_key)[1].lower()
        
        # Get original extraction goals and combine with new prompt
        original_goals = extraction_result.get("extraction_goals", "")
        if original_goals:
            # Combine original goals with new requirements
            new_extraction_goals = f"{original_goals}. {prompt}"
        else:
            new_extraction_goals = prompt
        

        # Start background job using the same function as first-time extraction
        import asyncio
        from services.file_extraction.background import process_extraction_background
        
        asyncio.create_task(process_extraction_background(
            job_id=new_job_id,
            file_key=file_key,
            file_extension=file_extension,
            extraction_goals=new_extraction_goals,
            user_id=user_id,
            flow_id=None,  # Re-extraction doesn't use reusable flows
            classification=None,  # No classification needed for re-extraction
            source_type="file",
            destination=destination,
            destination_name=destination_name,
            intent=intent_state.get("active_intent"),
            conversation_id=conversation_id,
            parent_job_id=original_job_id,
        ))
        
        from utils.job_stores import extraction_jobs
        from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal

        async def stream_generator():
            yield f"data: {json.dumps({'status': 'processing', 'source_type': 'file', 'job_id': new_job_id, 'message': 'Re-extraction started in background. Processing...'})}\n\n"
            async for evt in stream_extraction_job_events_until_terminal(new_job_id, extraction_jobs):
                yield f"data: {json.dumps(evt, default=str)}\n\n"

        return stream_generator()
        
    except Exception as e:
        print(f"❌ Error starting file re-extraction: {e}")
        import traceback
        traceback.print_exc()
        
        error_message = str(e)
        async def error_generator():
            yield f"data: {json.dumps({'status': 'error', 'message': f'Failed to start re-extraction: {error_message}'})}\n\n"
        return error_generator()
