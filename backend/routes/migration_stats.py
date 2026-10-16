"""Migration summary, dashboard stats, and successful/failed migration list endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from routes.auth import get_current_user

router = APIRouter()


@router.get("/migration-summary")
async def get_migration_summary(
    user_id: UUID = Depends(get_current_user)
):
    """Get migration summary statistics for the current user"""
    try:
        from services.migration_records_service import MigrationRecordsService
        
        summary = await MigrationRecordsService.get_migration_summary(str(user_id))
        
        return JSONResponse(content={"status": "success", "summary": summary})
        
    except Exception as e:
        print("❌ Failed to get migration summary:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard-stats")
async def get_dashboard_stats(
    user_id: UUID = Depends(get_current_user)
):
    """Get dashboard overview statistics for the current user"""
    try:
        from services.migration_records_service import MigrationRecordsService
        
        summary = await MigrationRecordsService.get_migration_summary(str(user_id))
        
        # Extract the key metrics for dashboard overview cards
        # Use only successful records to match the successful migrations page
        successful_records = summary.get("total_successful_records", 0)
        failed_records = summary.get("failed_records", 0)
        total_records = successful_records + failed_records
        
        dashboard_stats = {
            "total_migrations": summary.get("total_migrations", 0),
            "success_rate": summary.get("success_rate", 0),
            "total_records": total_records,  # Total attempted records
            "failed_records": failed_records,
            "successful_records": successful_records,  # Only successful records (matches successful page)
            "reusable_flows": summary.get("reusable_flows", 0)
        }
        
        return JSONResponse(content={
            "status": "success", 
            "dashboard_stats": dashboard_stats
        })
        
    except Exception as e:
        print("❌ Failed to get dashboard stats:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/latest-successful-migrations")
async def get_latest_successful_migrations(
    user_id: UUID = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=1000)
):
    """Get the latest successful migration records for the current user"""
    try:
        from services.migration_records_service import MigrationRecordsService
        
        records = await MigrationRecordsService.get_latest_successful_migrations(str(user_id), limit)
        
        return JSONResponse(content={
            "status": "success",
            "records": records
        })
        
    except Exception as e:
        print("❌ Failed to get latest successful migrations:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/recent-failed-migrations")
async def get_recent_failed_migrations(
    user_id: UUID = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=1000)
):
    """Get the recent failed migration records for the current user"""
    try:
        from services.migration_records_service import MigrationRecordsService
        
        records = await MigrationRecordsService.get_recent_failed_migrations(str(user_id), limit)
        
        return JSONResponse(content={
            "status": "success",
            "records": records
        })
        
    except Exception as e:
        print("❌ Failed to get recent failed migrations:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/successful-migrations")
async def get_successful_migrations(
    user_id: UUID = Depends(get_current_user),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
):
    """Get successful migration records with server-side pagination and optional search."""
    try:
        from services.migration_records_service import MigrationRecordsService

        result = await MigrationRecordsService.get_all_successful_migrations_paginated(
            user_id=str(user_id),
            page=page,
            page_size=page_size,
            search=search,
        )

        return JSONResponse(content={
            "status": "success",
            "records": result["records"],
            "total_count": result["total_count"],
            "page": result["page"],
            "page_size": result["page_size"],
        })

    except Exception as e:
        print("❌ Failed to get successful migrations:", str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/failed-migrations")
async def get_failed_migrations(
    user_id: UUID = Depends(get_current_user),
    page: int = 1,
    page_size: int = 50,
    search: Optional[str] = None,
):
    """Get failed migration records with server-side pagination and optional search."""
    try:
        from services.migration_records_service import MigrationRecordsService

        result = await MigrationRecordsService.get_all_failed_migrations_paginated(
            user_id=str(user_id),
            page=page,
            page_size=page_size,
            search=search,
        )

        return JSONResponse(content={
            "status": "success",
            "records": result["records"],
            "total_count": result["total_count"],
            "page": result["page"],
            "page_size": result["page_size"],
        })

    except Exception as e:
        print("❌ Failed to get failed migrations:", str(e))
        raise HTTPException(status_code=500, detail=str(e))
