"""First-time file extraction stream for agentic extract_file tool."""
from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import HTTPException

from handlers.classification_handler import classify_source_destination
from services.file_extraction.upload import (
    start_file_extraction_job,
    upload_file_bytes_to_s3,
    validate_and_prepare_file_upload,
)

logger = logging.getLogger(__name__)


async def handle_file_extract_stream(
    *,
    prompt: str,
    user_id: str,
    conversation_id: Optional[str],
    conversation_history: list,
    pending_file: Dict[str, Any],
    destination: str = "salesforce",
    destination_name: Optional[str] = None,
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> AsyncGenerator[str, None]:
    """
    Upload file, start background extraction, stream job events until terminal.
    Emits extraction_job_created, extraction_progress, extraction_job_finished.
    """
    filename = (pending_file.get("filename") or "upload").strip()
    file_content = pending_file.get("content")
    if not file_content or not isinstance(file_content, (bytes, bytearray)):
        yield f"data: {json.dumps({'status': 'error', 'message': 'No file content in session.'})}\n\n"
        return

    try:
        job_id, file_key, file_extension, _size_mb = await validate_and_prepare_file_upload(
            user_id=str(user_id),
            filename=filename,
            file_content=bytes(file_content),
        )
    except HTTPException as exc:
        detail = exc.detail
        msg = detail.get("message") if isinstance(detail, dict) else str(detail)
        yield f"data: {json.dumps({'status': 'error', 'message': msg})}\n\n"
        return
    except Exception as exc:
        yield f"data: {json.dumps({'status': 'error', 'message': str(exc)})}\n\n"
        return

    classification = dict(classification_snapshot or {})
    if not classification.get("source") or classification.get("source") == "unknown":
        try:
            classification = await classify_source_destination(
                prompt,
                user_id,
                conversation_history or [],
                conversation_id=conversation_id,
                explicit_source="file",
                explicit_source_name=filename,
            ) or classification
        except Exception as exc:
            logger.warning("file extract classify failed: %s", exc)

    classification["source"] = "file"
    classification["source_name"] = filename
    src_type = "file"
    dest = destination or classification.get("destination") or "salesforce"
    dest_name = destination_name or classification.get("destination_name")
    intent = classification.get("intent") or "extract"

    await upload_file_bytes_to_s3(file_key=file_key, file_content=bytes(file_content))

    await start_file_extraction_job(
        user_id=str(user_id),
        job_id=job_id,
        file_key=file_key,
        file_extension=file_extension,
        extraction_goals=prompt or "",
        conversation_id=conversation_id,
        classification=classification,
        source_type=src_type,
        destination=dest,
        destination_name=dest_name,
        intent=intent,
    )

    from utils.extraction_sse_stream import stream_extraction_job_events_until_terminal
    from utils.job_stores import extraction_jobs

    yield f"data: {json.dumps({'event_type': 'extraction_job_created', 'job_id': job_id, 'source_type': 'file'})}\n\n"

    async for evt in stream_extraction_job_events_until_terminal(job_id, extraction_jobs):
        yield f"data: {json.dumps(evt, default=str)}\n\n"
