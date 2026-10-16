"""Migration batch detail, paginated records, and rollback endpoints."""

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from db.connection import database
from routes.auth import get_current_user
from services.migration_records_service import MigrationRecordsService

router = APIRouter()


@router.get("/migration-records/batch/{batch_id}/records")
async def get_migration_records_paginated(
    batch_id: str,
    user_id: UUID = Depends(get_current_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    q: Optional[str] = Query(None, description="Optional search across all migration rows in batch"),
):
    """
    Get migration records with pagination. Mirrors extraction-results paginated API.
    Use for scalable display of large migration result sets.
    """
    result = await MigrationRecordsService.get_migration_records_paginated(
        batch_id=batch_id,
        user_id=str(user_id),
        page=page,
        page_size=page_size,
        search=q,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Migration batch not found")
    if result.get("detail_expired"):
        raise HTTPException(
            status_code=410,
            detail={
                "message": "Migration record detail is no longer available for this batch (retention policy).",
                "code": "MIGRATION_DETAIL_EXPIRED",
                "metadata": result.get("metadata"),
            },
        )
    if q and str(q).strip():
        print(
            f"[PREVIEW_SEARCH] migration batch_id={batch_id} page={page} "
            f"q={q!r} matches={result.get('total_count')}"
        )
    # Format records for InlineMigrationRecords (source_data, mapped_data, salesforce_results, etc.)
    formatted = []
    for r in result["records"]:
        formatted.append({
            "source_data": r.get("source_data") or {},
            "mapped_data": r.get("mapped_data") or {},
            "salesforce_results": r.get("salesforce_results") or {},
            "status": r.get("status", "unknown"),
            "error_message": r.get("error_message"),
            "id": r.get("id"),
            "record_index": r.get("record_index"),
        })
    return {
        "status": "success",
        "metadata": result["metadata"],
        "records": formatted,
        "total_count": result["total_count"],
        "page": result["page"],
        "page_size": result["page_size"],
    }


@router.get("/migration-records/batch/{batch_id}")
async def get_migration_records_by_batch(
    batch_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """Get all migration records for a specific batch, formatted for InlineDataPreview"""
    try:
        from services.migration_records_service import MigrationRecordsService
        
        # Verify user ownership by checking batch and get batch status (before processing records)
        batch_query = """
        SELECT user_id, status, batch_type, mapping_config, total_records, detail_purged_at
        FROM migration_batches WHERE id = :batch_id
        """
        batch_result = await database.fetch_one(batch_query, values={"batch_id": batch_id})
        
        if not batch_result:
            raise HTTPException(status_code=404, detail="Migration batch not found")
        
        # Convert result to dict for safe access (databases library returns Record objects)
        batch_dict = dict(batch_result) if batch_result else {}
        
        if str(batch_dict.get("user_id")) != str(user_id):
            raise HTTPException(status_code=403, detail="Access denied to this migration batch")

        sync_outcome = None
        try:
            outcome_row = await database.fetch_one(
                """
                SELECT sync_outcome
                FROM flow_executions
                WHERE migration_batch_id = :batch_id
                  AND user_id = :user_id
                ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT 1
                """,
                values={"batch_id": batch_id, "user_id": str(user_id)},
            )
            if outcome_row:
                sync_outcome = dict(outcome_row).get("sync_outcome")
        except Exception as outcome_error:
            print(f"⚠️ Failed to fetch sync_outcome for batch {batch_id}: {outcome_error}")

        dp = batch_dict.get("detail_purged_at")
        if dp is not None:
            mapping_config = batch_dict.get("mapping_config")
            if isinstance(mapping_config, str):
                try:
                    mapping_config = json.loads(mapping_config) if mapping_config else {}
                except Exception:
                    mapping_config = {}
            elif mapping_config is None:
                mapping_config = {}
            purged_iso = dp.isoformat() if hasattr(dp, "isoformat") else str(dp)
            total_in_batch = batch_dict.get("total_records") or 0
            return JSONResponse(content={
                "status": "success",
                "batch_id": batch_id,
                "batch_status": batch_dict.get("status"),
                "sync_outcome": sync_outcome,
                "batch_type": batch_dict.get("batch_type") or "data",
                "mapping_config": mapping_config,
                "records": [],
                "total_records": total_in_batch,
                "detail_available": False,
                "detail_expired": True,
                "detail_purged_at": purged_iso,
            })
        
        batch_status = batch_dict.get("status")
        total_in_batch = batch_dict.get("total_records") or 0
        batch_type = batch_dict.get("batch_type") or "data"

        # Use iteration for large batches to avoid OOM; cap at 5000 for this endpoint
        MAX_RECORDS_RETURN = 5000
        records = []
        truncated = False
        if total_in_batch > MAX_RECORDS_RETURN:
            async for rec_batch in MigrationRecordsService.iterate_migration_records(
                batch_id, batch_size=500
            ):
                for r in rec_batch:
                    records.append(r)
                    if len(records) >= MAX_RECORDS_RETURN:
                        truncated = True
                        break
                if truncated:
                    break
        else:
            records = await MigrationRecordsService.get_migration_records(batch_id)
        mapping_config = batch_dict.get("mapping_config")
        if isinstance(mapping_config, str):
            try:
                mapping_config = json.loads(mapping_config) if mapping_config else {}
            except Exception:
                mapping_config = {}
        elif mapping_config is None:
            mapping_config = {}
        
        if not records:
            return JSONResponse(content={
                "status": "success",
                "batch_id": batch_id,
                "batch_status": batch_status,
                "sync_outcome": sync_outcome,
                "batch_type": batch_type,
                "mapping_config": mapping_config,
                "records": [],
                "total_records": total_in_batch or 0,
                "detail_available": True,
                "detail_expired": False,
                "detail_purged_at": None,
            })
        
        # Format records for InlineMigrationRecords component
        # Return records with: source_data, mapped_data, salesforce_results, status, error_message at top level
        formatted_records = []
        for record in records:
            # Parse source_data and mapped_data
            source_data = record.get("source_data", {})
            if isinstance(source_data, str):
                import json
                try:
                    source_data = json.loads(source_data)
                except:
                    source_data = {}
            
            mapped_data = record.get("mapped_data", {})
            if isinstance(mapped_data, str):
                import json
                try:
                    mapped_data = json.loads(mapped_data)
                except:
                    mapped_data = {}
            
            # Parse salesforce_results
            salesforce_results = record.get("salesforce_results")
            if salesforce_results:
                if isinstance(salesforce_results, str):
                    import json
                    try:
                        salesforce_results = json.loads(salesforce_results)
                    except:
                        salesforce_results = {}
            else:
                salesforce_results = {}
            
            # Build formatted record with all fields at top level (no underscores)
            formatted_record = {
                "source_data": source_data,
                "mapped_data": mapped_data,
                "salesforce_results": salesforce_results,
                "status": record.get("status", "unknown"),
                "error_message": record.get("error_message"),
                "id": record.get("id"),
                "record_index": record.get("record_index"),
            }
            
            formatted_records.append(formatted_record)
        
        response = {
            "status": "success",
            "batch_id": batch_id,
            "batch_status": batch_status,
            "sync_outcome": sync_outcome,
            "batch_type": batch_type,
            "mapping_config": mapping_config,
            "records": formatted_records,
            "total_records": total_in_batch or len(formatted_records),
            "detail_available": True,
            "detail_expired": False,
            "detail_purged_at": None,
        }
        if truncated:
            response["truncated"] = True
            response["message"] = (
                f"Showing first {len(formatted_records)} of {total_in_batch} records. "
                f"Use GET /migration-records/batch/{batch_id}/records?page=1&page_size=100 for paginated access."
            )
        return JSONResponse(content=response)
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Failed to get migration records for batch {batch_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/migration-records/rollback/{batch_id}")
async def rollback_migration_batch(
    batch_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """
    Rollback all created records in a migration batch.
    Only deletes records with status = "created" (not "updated").
    
    Request:
    - batch_id: The batch ID to rollback
    
    Response:
    {
        "status": "success" | "partial_success" | "failed",
        "message": "Rollback completed successfully",
        "total_records": 100,
        "deleted_records": 95,
        "failed_records": 5,
        "errors": [...]
    }
    """
    try:
        from services.rollback_service import RollbackService
        
        result = await RollbackService.rollback_batch(
            batch_id=batch_id,
            user_id=str(user_id)
        )

        if result.get("errors") == ["insufficient_credits"] or "Insufficient credits" in (result.get("message") or ""):
            return JSONResponse(content=result, status_code=402)
        
        return JSONResponse(content=result)
        
    except Exception as e:
        print(f"❌ Error rolling back migration batch: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
