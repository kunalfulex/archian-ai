"""Mapping destination catalog endpoints for UI pickers."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from routes.auth import get_current_user
from services.mapping_plan.destination_targets_api import list_destination_targets

router = APIRouter(prefix="/api/mapping", tags=["mapping"])


@router.get("/destination-targets")
async def get_destination_targets(
    destination: str = Query(..., min_length=1),
    destination_name: str = Query(..., min_length=1),
    user_id: UUID = Depends(get_current_user),
):
    """Objects / tables / sheet tabs for a selected connection (synced catalog only)."""
    try:
        payload = await list_destination_targets(
            user_id=user_id,
            destination=destination,
            destination_name=destination_name,
        )
        return JSONResponse(content={"status": "success", **payload})
    except Exception as exc:
        return JSONResponse(
            content={"status": "error", "message": str(exc), "targets": []},
            status_code=500,
        )
