"""Super-admin data retention and user listing endpoints."""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from db.connection import database
from routes.auth import get_super_admin_user

router = APIRouter()


@router.post("/admin/data-retention/run")
async def admin_run_data_retention(
    user_id: Optional[str] = Query(
        None,
        description="If set, only run retention purge for this user. Omit to process every user.",
    ),
    _: UUID = Depends(get_super_admin_user),
):
    """Manually trigger data retention purge (same logic as the scheduled job). Super admin only."""
    from services.data_retention_service import DataRetentionService

    try:
        if user_id:
            summary = await DataRetentionService.purge_for_user(user_id)
            return JSONResponse(
                content={
                    "status": "success",
                    "scope": "single_user",
                    "user_id": user_id,
                    "summary": summary,
                }
            )
        summary = await DataRetentionService.purge_all_users()
        return JSONResponse(
            content={
                "status": "success",
                "scope": "all_users",
                "summary": summary,
            }
        )
    except Exception as e:
        print(f"❌ admin data retention run failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/users")
async def get_admin_users(
    q: str = Query("", min_length=0),
    limit: int = Query(20, ge=1, le=50),
    _: UUID = Depends(get_super_admin_user),
):
    """Search users by email or name for admin. Super admin only."""
    try:
        if not q or len(q.strip()) < 2:
            return JSONResponse(content={"status": "success", "users": []})
        search = f"%{q.strip()}%"
        rows = await database.fetch_all(
            """
            SELECT id, email, first_name, last_name
            FROM users
            WHERE is_deleted = FALSE
              AND (email ILIKE :search OR first_name ILIKE :search OR last_name ILIKE :search)
            ORDER BY email
            LIMIT :limit
            """,
            values={"search": search, "limit": limit},
        )
        users = []
        for r in rows:
            users.append({
                "id": str(r["id"]),
                "email": r["email"],
                "first_name": getattr(r, "first_name", None) or "",
                "last_name": getattr(r, "last_name", None) or "",
            })
        return JSONResponse(content={"status": "success", "users": users})
    except Exception as e:
        print(f"❌ Error searching users: {e}")
        raise HTTPException(status_code=500, detail="Error searching users")
