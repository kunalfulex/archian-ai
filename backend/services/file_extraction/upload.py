"""Shared file upload + background extraction job start (chat agentic + legacy upload-async)."""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException

ALLOWED_FILE_EXTENSIONS = frozenset({".pdf", ".csv", ".txt", ".xlsx", ".jpg", ".jpeg", ".png"})


async def validate_and_prepare_file_upload(
    *,
    user_id: str,
    filename: str,
    file_content: bytes,
) -> Tuple[str, str, str, float]:
    """
    Validate plan limits and extension. Returns (job_id, file_key, file_extension, size_mb).
  Raises HTTPException on validation failure.
    """
    from services.entitlements import get_entitlements  # noqa: PLC0415

    file_size_mb = len(file_content) / (1024 * 1024)
    is_valid, message, _plan_info = await get_entitlements().validate_file_upload(
        str(user_id), file_size_mb
    )
    if not is_valid:
        raise HTTPException(
            status_code=403,
            detail={
                "status": "error",
                "message": message,
                "file_size_mb": round(file_size_mb, 2),
            },
        )

    _, file_extension = os.path.splitext(filename or "")
    file_extension = (file_extension or "").lower()
    if file_extension not in ALLOWED_FILE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail="Only PDF, CSV, XLSX, TXT, JPG, JPEG, and PNG files are supported.",
        )

    job_id = str(uuid.uuid4())
    file_key = f"{job_id}_{filename}"
    return job_id, file_key, file_extension, file_size_mb


def _s3_bucket_name() -> str:
    return os.getenv("S3_BUCKET_NAME") or os.getenv("AWS_S3_BUCKET") or "edisonaistorage"


async def upload_file_bytes_to_s3(*, file_key: str, file_content: bytes) -> None:
    import boto3

    bucket = _s3_bucket_name()
    client = boto3.client("s3")
    await asyncio.to_thread(
        client.put_object,
        Bucket=bucket,
        Key=file_key,
        Body=file_content,
    )


async def start_file_extraction_job(
    *,
    user_id: str,
    job_id: str,
    file_key: str,
    file_extension: str,
    extraction_goals: str,
    conversation_id: Optional[str],
    classification: Optional[Dict[str, Any]] = None,
    source_type: str = "file",
    destination: str = "salesforce",
    destination_name: Optional[str] = None,
    intent: Optional[str] = None,
    flow_id: Optional[str] = None,
    parent_job_id: Optional[str] = None,
    log_extraction_started: bool = True,
) -> None:
    """Upload already on S3; enqueue process_extraction_background and optional chat event."""
    from utils.task_limiter import create_limited_task
    from services.file_extraction.background import process_extraction_background
    from services.chat_service import ChatService
    from handlers.classification_handler import create_intent_state

    classification_result = dict(classification or {})
    src_type = source_type or "file"
    dest = destination or classification_result.get("destination") or "salesforce"
    dest_name = destination_name or classification_result.get("destination_name")
    active_intent = intent or classification_result.get("intent") or "extract"

    if log_extraction_started and conversation_id:
        try:
            source_name = file_key.split("_", 1)[1] if "_" in file_key else file_key
            if src_type != "file":
                source_name = classification_result.get("source_name") or source_name

            intent_state = create_intent_state(
                active_intent=active_intent,
                active_source=src_type,
                active_destination=dest,
                source_name=source_name,
                destination_name=dest_name,
                job_id=job_id,
                state="extracting",
            )
            await ChatService.log_data_event(
                conversation_id=conversation_id,
                user_id=str(user_id),
                event_type="extraction_started",
                event_data={
                    "job_id": job_id,
                    "source_type": src_type,
                    "source_name": source_name,
                    "file_name": source_name,
                    "destination": dest,
                    "destination_name": dest_name,
                    "intent_state": intent_state,
                },
            )
        except Exception:
            pass

    await create_limited_task(
        process_extraction_background(
            job_id=job_id,
            file_key=file_key,
            file_extension=file_extension,
            extraction_goals=extraction_goals or "",
            user_id=str(user_id),
            flow_id=flow_id,
            classification=classification_result,
            source_type=src_type,
            destination=dest,
            destination_name=dest_name,
            intent=active_intent,
            conversation_id=conversation_id,
            parent_job_id=parent_job_id,
        )
    )
