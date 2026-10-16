"""User profile, timezones, and data-retention preference endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from routes.auth import get_current_user
from services.profile_management_service import ProfileManagementService

router = APIRouter()


class DataRetentionUpdateRequest(BaseModel):
    data_retention_days: int


@router.get("/profile")
async def get_user_profile(
    user_id: UUID = Depends(get_current_user)
):
    """Get user profile information"""
    try:
        profile = await ProfileManagementService.get_user_profile(str(user_id))
        
        if not profile:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "User profile not found"
                },
                status_code=404
            )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "Profile retrieved successfully",
                "data": profile
            }
        )
        
    except Exception as e:
        print(f"❌ Error getting user profile: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting user profile: {str(e)}")


@router.put("/profile")
async def update_user_profile(
    request: Request,
    user_id: UUID = Depends(get_current_user)
):
    """Update user profile information"""
    try:
        # Get profile data from request body
        body = await request.json()
        
        # Validate profile data
        is_valid, validation_message = await ProfileManagementService.validate_profile_data(body)
        if not is_valid:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": validation_message
                },
                status_code=400
            )
        
        # Update profile
        success, message, updated_profile = await ProfileManagementService.update_user_profile(
            str(user_id), body
        )
        
        if not success:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": message
                },
                status_code=400
            )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": message,
                "data": updated_profile
            }
        )
        
    except Exception as e:
        print(f"❌ Error updating user profile: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating user profile: {str(e)}")


@router.get("/user/data-retention")
async def get_data_retention_settings(user_id: UUID = Depends(get_current_user)):
    """Return stored retention preference and plan cap (standard: max 30 days; enterprise/custom: up to 365)."""
    from services.data_retention_service import DataRetentionService

    data = await DataRetentionService.get_settings(str(user_id))
    return JSONResponse(
        content={
            "status": "success",
            "data": data,
        }
    )


@router.patch("/user/data-retention")
async def patch_data_retention_settings(
    request: DataRetentionUpdateRequest,
    user_id: UUID = Depends(get_current_user),
):
    from services.data_retention_service import DataRetentionService

    ok, msg = await DataRetentionService.validate_and_update_retention_days(
        str(user_id), request.data_retention_days
    )
    if not ok:
        return JSONResponse(content={"status": "error", "message": msg}, status_code=400)
    data = await DataRetentionService.get_settings(str(user_id))
    return JSONResponse(
        content={
            "status": "success",
            "message": "Data retention updated",
            "data": data,
        }
    )


@router.get("/profile/timezones")
async def get_available_timezones():
    """Get list of available timezones"""
    try:
        timezones = await ProfileManagementService.get_available_timezones()
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "Timezones retrieved successfully",
                "data": timezones
            }
        )
        
    except Exception as e:
        print(f"❌ Error getting timezones: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting timezones: {str(e)}")
