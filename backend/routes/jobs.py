"""Job status, background migration results, and async file upload endpoints."""

import asyncio
import json
import os
import uuid
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from db.connection import database
from handlers.classification_handler import classify_source_destination
from routes.auth import get_current_user
from services.chat_service import ChatService
from services.entitlements import get_entitlements
from services.file_extraction.background import process_extraction_background
from services.file_store import put_bytes
from services.flow_migration_background import get_background_job
from services.migration_records_service import MigrationRecordsService
from utils.job_stores import extraction_jobs, sql_tasks
from utils.task_limiter import create_limited_task

router = APIRouter()


@router.get("/job-status/{job_id}")
async def get_job_status(job_id: str, user_id: UUID = Depends(get_current_user)):
    """
    Get the status of a background extraction or migration job
    """

    # First check extraction_jobs (Redis-backed)
    if await extraction_jobs.exists(job_id):
        job = await extraction_jobs.get(job_id)
        
        result = job.get("result")
        
        response_data = {
            "job_id": job_id,
            "status": job["status"],
            "progress": job.get("progress", 0),
            "result": result,
            "error": job.get("error"),
            "error_explanation": job.get("error_explanation"),
            "error_solution": job.get("error_solution"),
            "created_at": job.get("created_at"),
            "extraction_goals": job.get("extraction_goals", ""),
            "flow_id": job.get("flow_id"),
            "destination": job.get("destination"),
            "destination_name": job.get("destination_name"),
            "job_type": job.get("job_type"),
            "source_type": job.get("source_type"),
            "parent_job_id": job.get("parent_job_id"),
            "connection_name": job.get("connection_name"),  # Salesforce org name
            "org_id": job.get("org_id"),
            "salesforce_token_id": job.get("salesforce_token_id"),
            "object_api_name": job.get("object_api_name"),
            "spreadsheet_name": job.get("spreadsheet_name"),  # Google Sheets spreadsheet name
            "file_key": job.get("file_key"),
        }

        # Ensure all values are JSON serializable
        response_data = json.loads(json.dumps(response_data, default=str))
        return JSONResponse(content=response_data)

    # SQL extractions (Redis sql_tasks — same job_id contract as extraction_jobs)
    if await sql_tasks.exists(job_id):
        from utils.sql_job_status import build_sql_job_status_response

        response_data = await build_sql_job_status_response(job_id, user_id, sql_tasks)
        return JSONResponse(content=response_data)
    
    # If not found in extraction_jobs, check migration jobs
    migration_job, job_type = await get_background_job(job_id)
    if migration_job and migration_job.get("status") != "unknown":
        # Format migration job response
        failed_records = migration_job.get("failed_records", 0)
        batch_id = migration_job.get("batch_id")
        result_payload: Dict[str, Any] = {
            "status": migration_job.get("status"),
            "total_records": migration_job.get("total_records", 0),
            "processed_records": migration_job.get("processed_records", 0),
            "successful_records": migration_job.get("successful_records", 0),
            "failed_records": failed_records,
            "batch_id": batch_id,
            "instance_url": migration_job.get("instance_url"),
            "spreadsheet_id": migration_job.get("spreadsheet_id"),
        }
        if (
            migration_job.get("status") in ("completed", "success")
            and failed_records
            and batch_id
        ):
            try:
                from services.migration.failure_summary import build_failure_summary_for_batch

                failure_summary = await build_failure_summary_for_batch(str(batch_id))
                if failure_summary:
                    result_payload["failure_summary"] = failure_summary
            except Exception:
                pass

        response_data = {
            "job_id": job_id,
            "status": migration_job.get("status", "processing"),
            "progress": migration_job.get("processed_records", 0) / max(migration_job.get("total_records", 1), 1) * 100 if migration_job.get("total_records") else 0,
            "result": result_payload,
            "error": migration_job.get("error_summary"),
            "created_at": migration_job.get("started_at"),
            "destination": job_type,  # "salesforce", "google_sheets", or "sql"
            "destination_name": migration_job.get("destination_name"),
            "job_type": "migration",
            "source_type": None,
            "batch_id": migration_job.get("batch_id"),
            "org_id": migration_job.get("org_id"),
            "instance_url": migration_job.get("instance_url"),
            "spreadsheet_id": migration_job.get("spreadsheet_id"),
            "connector_id": migration_job.get("connector_id")
            or migration_job.get("destination_connector_id"),
            "source_job_id": migration_job.get("source_job_id"),
        }
        
        # Ensure all values are JSON serializable
        response_data = json.loads(json.dumps(response_data, default=str))
        return JSONResponse(content=response_data)
    
    # Job not found in either extraction_jobs or migration services
    raise HTTPException(status_code=404, detail="Job not found")


@router.post("/upload-async/")
async def upload_file_async(
    user_id: UUID = Depends(get_current_user),
    file: UploadFile = File(...),
    extraction_goals: str = Form(""),
    flow_id: str = Form(None),
    conversation_id: Optional[str] = Form(None)
):
    """
    Upload file and start background extraction job
    Returns job ID immediately for non-blocking processing
    """
    try:
        # Generate unique job ID
        job_id = str(uuid.uuid4())
        
        # Plan validation: Check file size before processing
        file_content = await file.read()
        file_size_mb = len(file_content) / (1024 * 1024)  # Convert bytes to MB
        
        # Validate file size against user's plan
        is_valid, message, plan_info = await get_entitlements().validate_file_upload(str(user_id), file_size_mb)
        if not is_valid:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": message,
                    "plan_info": plan_info,
                    "file_size_mb": round(file_size_mb, 2)
                },
                status_code=403
            )
        
        # Classify destination/source intent from the user's prompt (best-effort)
        classification_result: Dict[str, Any] = {}
        source_type = "file"
        destination = "salesforce"
        destination_name = None
        intent = None

        if not flow_id and extraction_goals and extraction_goals.strip():
            try:
                classification_result = await classify_source_destination(
                    extraction_goals,
                    str(user_id),
                    [],
                    conversation_id=conversation_id,
                    explicit_source="file",
                    explicit_source_name=file.filename
                ) or {}
                print(f"🔍 Classification result: {classification_result}")
                detected_source = classification_result.get("source")
                if detected_source and detected_source.lower() != "unknown":
                    source_type = detected_source

                detected_destination = classification_result.get("destination")
                if detected_destination and detected_destination.lower() != "unknown":
                    destination = detected_destination

                destination_name = classification_result.get("destination_name") or destination_name
                intent = classification_result.get("intent")
            except Exception as classification_error:
                print(f"⚠️ Classification error for job {job_id}: {classification_error}")
                classification_result = {}

        # Persist upload (local disk or S3 via file_store)
        file_key = f"{job_id}_{file.filename}"
        await put_bytes(file_key=file_key, file_content=file_content)

        _, file_extension = os.path.splitext(file.filename)
        file_extension = file_extension.lower()

        if file_extension not in [".pdf", ".csv", ".txt", ".xlsx", ".jpg", ".jpeg", ".png"]:
            raise HTTPException(status_code=400, detail="Only PDF, CSV, XLSX, TXT, JPG, JPEG, and PNG files are supported.")

        # Log extraction started event if conversation_id is provided
        if conversation_id:
            try:
                from handlers.classification_handler import create_intent_state
                
                # Get classification info for intent_state
                active_intent = classification_result.get("intent", "extract")
                active_source = classification_result.get("source", source_type)
                active_destination = classification_result.get("destination", destination or "unknown")
                # Use file.filename as source_name (S3 bucket filename) only when source_type is "file"
                # For other source types, use classification_result source_name or None
                if source_type == "file":
                    source_name = file.filename
                else:
                    source_name = classification_result.get("source_name")
                destination_name_param = classification_result.get("destination_name") or destination_name
                
                intent_state = create_intent_state(
                    active_intent=active_intent,
                    active_source=active_source,
                    active_destination=active_destination,
                    source_name=source_name,
                    destination_name=destination_name_param,
                    job_id=job_id,
                    state="extracting"
                )
                
                await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=str(user_id),
                    event_type="extraction_started",
                    event_data={
                        "job_id": job_id,
                        "source_type": source_type,
                        "source_name": source_name,
                        "expected_record_count": None,  # Unknown at this point
                        "file_name": file.filename,
                        "file_size_mb": round(file_size_mb, 2),
                        "destination": active_destination,
                        "destination_name": destination_name_param,
                        "intent_state": intent_state
                    }
                )
            except Exception as e:
                print(f"⚠️ Failed to log extraction_started event: {e}")

        # Start background job with concurrency limiting
        await create_limited_task(process_extraction_background(
            job_id=job_id,
            file_key=file_key,
            file_extension=file_extension,
            extraction_goals=extraction_goals,
            user_id=str(user_id),
            flow_id=flow_id,
            classification=classification_result,
            source_type=source_type,
            destination=destination,
            destination_name=destination_name,
            intent=intent,
            conversation_id=conversation_id,
        ))

        # Return job ID immediately
        response_data = {
            "status": "processing",
            "job_id": job_id,
            "message": "File uploaded successfully. Extraction started in background.",
            "file_key": file_key,
            "file_size_mb": round(file_size_mb, 2),
            "destination": destination,
            "destination_name": destination_name,
            "source_type": source_type,
        }

        return JSONResponse(content=response_data)

    except Exception as e:
        print("Async upload error:", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/background-migration-results/{job_id}")
async def get_background_migration_results(
    job_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """Get the actual Salesforce results (IDs, statuses) for a completed background migration job"""
    try:
        job_status, job_type = await get_background_job(job_id)
        
        # Get the batch ID from the job (either from memory or database)
        batch_id = None
        if job_status:
            batch_id = job_status.get("batch_id")
        
        # If job is not in memory, try to find the batch_id from the database
        if not batch_id:

            batch_query = """
            SELECT id as batch_id, batch_name, document_type, total_records, status, created_at
            FROM migration_batches 
            WHERE user_id = :user_id 
            AND batch_name LIKE :job_pattern
            ORDER BY created_at DESC 
            LIMIT 1
            """
            
            # Try to find batch by looking for batch names that might contain the job_id
            batch_result = await database.fetch_one(
                batch_query, 
                values={
                    "user_id": str(user_id), 
                    "job_pattern": f"%{job_id}%"
                }
            )
            
            if batch_result:
                batch_id = batch_result["batch_id"]
                # Found batch_id for job
            else:
                # If still not found, try to get the most recent batch for this user
                recent_batch_query = """
                SELECT id as batch_id, batch_name, document_type, total_records, status, created_at
                FROM migration_batches 
                WHERE user_id = :user_id 
                ORDER BY created_at DESC 
                LIMIT 1
                """
                
                recent_batch_result = await database.fetch_one(
                    recent_batch_query, 
                    values={"user_id": str(user_id)}
                )
                
                if recent_batch_result:
                    batch_id = recent_batch_result["batch_id"]
                    # Using most recent batch_id for job
                else:
                    raise HTTPException(status_code=404, detail="No migration batches found for this user")

        if not job_status:
            job_status = {}
        
        if not batch_id:
            raise HTTPException(status_code=404, detail="Batch ID not found for this job")
        
        # Get migration records - use iteration for large batches to avoid OOM
        migration_records = []
        async for rec_batch in MigrationRecordsService.iterate_migration_records(
            batch_id, batch_size=500
        ):
            migration_records.extend(rec_batch)
            if len(migration_records) >= 5000:  # Cap for response size
                break

        if not migration_records:
            raise HTTPException(status_code=404, detail="No migration records found for this batch")
        
        # With single batch approach, we don't need to link batches anymore
        # Getting results from single batch
        
        # Extract migration results from the single batch
        salesforce_results = []
        for record in migration_records:
            # record is now a dictionary, not an object
            if record.get("salesforce_results"):
                # Parse the JSON string back to dict
                try:
                    sf_results = json.loads(record["salesforce_results"]) if isinstance(record["salesforce_results"], str) else record["salesforce_results"]
                    
                    # Extract the actual ID and status
                    for obj_name, obj_result in sf_results.items():
                        if isinstance(obj_result, dict):
                            if obj_result.get("status") == "error":
                                message = obj_result.get("message", "Migration failed")
                            elif obj_result.get("status") == "updated":
                                if job_type == "google_sheets":
                                    message = "Successfully updated in Google Sheets"
                                elif job_type == "sql":
                                    message = "Successfully updated in Database"
                                else:
                                    message = "Successfully updated in Salesforce"
                            elif obj_result.get("status") == "created":
                                if job_type == "google_sheets":
                                    message = "Successfully created in Google Sheets"
                                elif job_type == "sql":
                                    message = "Successfully created in Database"
                                else:
                                    message = "Successfully created in Salesforce"
                            else:
                                message = f"Status: {obj_result.get('status', 'unknown')}"
                            
                            salesforce_results.append({
                                "status": obj_result.get("status", "unknown"),
                                "salesforce_id": obj_result.get("id"),
                                "message": message,
                                "record_index": record["record_index"],
                                "source_data": record["source_data"],
                                "salesforce_results": record["salesforce_results"],  # Include full results for frontend
                                "object_name": obj_name
                            })
                            break  # Only take the first object result
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"⚠️ Error parsing salesforce_results for record {record.get('id')}: {e}")
                    # Fallback
                    salesforce_results.append({
                        "status": "error",
                        "salesforce_id": None,
                        "message": "Error parsing Salesforce results",
                        "record_index": record["record_index"],
                        "source_data": record["source_data"],
                        "salesforce_results": record["salesforce_results"],
                        "object_name": "Unknown"
                    })
            else:
                # No Salesforce results yet
                salesforce_results.append({
                    "status": "pending",
                    "salesforce_id": None,
                    "message": "Record not yet processed",
                    "record_index": record["record_index"],
                    "source_data": record["source_data"],
                    "salesforce_results": record.get("salesforce_results"),
                    "object_name": "Unknown"
                })
        
        # Sort by record index to maintain order
        salesforce_results.sort(key=lambda x: x["record_index"])
        
        # Get destination URL
        instance_url = None
        if job_type == "google_sheets":
            spreadsheet_id = job_status.get("spreadsheet_id")
            if spreadsheet_id:
                instance_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}"
        elif job_type == "salesforce":
            try:
                instance_query = """
                SELECT instance_url FROM salesforce_tokens 
                WHERE user_id = :user_id 
                ORDER BY issued_at DESC 
                LIMIT 1
                """
                instance_result = await database.fetch_one(instance_query, values={"user_id": str(user_id)})
                if instance_result:
                    instance_url = instance_result["instance_url"]
            except Exception as e:
                print(f"⚠️ Error getting instance URL: {e}")
        
        response_data = {
            "status": "success",
            "job_id": job_id,
            "batch_id": batch_id,
            "salesforce_results": salesforce_results,
            "total_records": len(salesforce_results),
            "job_status": job_status.get("status"),
            "salesforce_instance_url": instance_url,
            "job_type": job_type,
            "destination_name": job_status.get("destination_name"),
            "spreadsheet_id": job_status.get("spreadsheet_id"),
            "connector_id": job_status.get("connector_id"),
            "table_name": job_status.get("table_name"),
        }
        if job_type == "google_sheets":
            response_data["spreadsheet_url"] = instance_url
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        print("❌ Failed to get background migration results:", str(e))
        raise HTTPException(status_code=500, detail=str(e))
