"""Extraction results preview, pagination, CSV export, and conversation listing."""

import re
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from routes.auth import get_current_user
from services.chat_service import ChatService
from services.extraction_results_service import ExtractionResultsService

router = APIRouter()


@router.get("/extraction-results/{job_id}")
async def get_extraction_result(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """
    Get full extraction result by job_id or extraction_results.id (includes full extracted_data).
    Used when user clicks to show data preview.
    """
    try:
        result = await ExtractionResultsService.get_extraction_result(job_id, str(user_id))
        if not result:
            raise HTTPException(status_code=404, detail="Extraction result not found")
        return {"status": "success", "result": result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Failed to get extraction result: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extraction-results/{job_id}/records")
async def get_extraction_records_paginated(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=1000, description="Records per page"),
    q: Optional[str] = Query(None, description="Optional search across all stored rows"),
):
    """
    Get extraction records with pagination.
    Path parameter may be async job_id or extraction_results.id.
    Prefers extraction_result_records for scalable access; falls back to extracted_data.
    """
    try:
        result = await ExtractionResultsService.get_extraction_records_paginated(
            job_id=job_id,
            user_id=str(user_id),
            page=page,
            page_size=page_size,
            search=q,
        )
        if not result:
            raise HTTPException(status_code=404, detail="Extraction result not found")
        if result.get("detail_expired"):
            raise HTTPException(
                status_code=410,
                detail={
                    "message": "Extraction detail is no longer available for this job (retention policy).",
                    "code": "EXTRACTION_DETAIL_EXPIRED",
                    "metadata": result.get("metadata"),
                },
            )
        if q and str(q).strip():
            print(
                f"[PREVIEW_SEARCH] extraction job_id={job_id} page={page} "
                f"q={q!r} matches={result.get('total_count')}"
            )
        return {"status": "success", **result}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Failed to get extraction records: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/extraction-results/{job_id}/export.csv")
async def export_extraction_results_csv(
    job_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """
    Stream full extracted records as CSV (all rows in extraction_result_records or legacy extracted_data).
    Used for inline preview download when record count exceeds the client-side preview page size.
    """
    resolved = await ExtractionResultsService.resolve_job_id(job_id, str(user_id))
    if not resolved:
        raise HTTPException(status_code=404, detail="Extraction result not found")

    if await ExtractionResultsService.is_extraction_detail_expired(job_id, str(user_id)):
        raise HTTPException(
            status_code=410,
            detail={
                "message": "Extraction detail is no longer available for this job (retention policy).",
                "code": "EXTRACTION_DETAIL_EXPIRED",
            },
        )

    safe_job = re.sub(r"[^a-zA-Z0-9_.-]", "_", resolved)[:100]
    filename = f"extraction-{safe_job}.csv"

    async def byte_stream():
        async for chunk in ExtractionResultsService.iter_extraction_csv_bytes(resolved, str(user_id)):
            yield chunk

    return StreamingResponse(
        byte_stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/extraction-results/conversation/{conversation_id}")
async def get_extraction_results_by_conversation(
    conversation_id: str,
    user_id: UUID = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
):
    """
    Get metadata-only extraction results for a conversation.
    Returns metadata (job_id, record_count, created_at, etc.) without full extracted_data.
    Full data is fetched on-demand when user clicks to show preview.
    """
    try:
        # Verify conversation belongs to user
        conversation = await ChatService.get_conversation(conversation_id, str(user_id))
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        results = await ExtractionResultsService.get_extraction_results_by_conversation(
            conversation_id,
            str(user_id),
            limit=limit,
            conversation_verified=True,
        )
        
        # Return metadata only (remove extracted_data to reduce payload size)
        metadata_results = []
        for result in results:
            metadata_results.append({
                "id": result["id"],
                "job_id": result["job_id"],
                "parent_job_id": result["parent_job_id"],
                "source_type": result["source_type"],
                "source_name": result["source_name"],
                "source_identifier": result["source_identifier"],
                "extraction_goals": result["extraction_goals"],
                "document_type": result["document_type"],
                "document_description": result["document_description"],
                "record_count": result["record_count"],
                "extraction_version": result["extraction_version"],
                "has_cleanup_rules": result["has_cleanup_rules"],
                "has_transformation_rules": result["has_transformation_rules"],
                "created_at": result["created_at"],
                # Note: extracted_data is excluded - fetch via /extraction-results/{job_id} when needed
            })
        
        return {"status": "success", "results": metadata_results}
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Failed to get extraction results by conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
