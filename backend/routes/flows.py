"""Reusable flow schedule and execution listing endpoints."""

import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db.connection import database
from routes.auth import get_current_user
from services.flow_executions_service import FlowExecutionsService

router = APIRouter()


@router.get("/flow/{flow_id}/executions")
async def list_flow_executions(
    flow_id: str,
    user_id: UUID = Depends(get_current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List flow run history (Data Timeline) for a saved flow."""
    flow = await database.fetch_one(
        """
        SELECT id FROM migration_flows
        WHERE id = :flow_id AND user_id = :user_id
        """,
        {"flow_id": flow_id, "user_id": str(user_id)},
    )
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    # Only this flow's executions — reverse-leg runs belong on that flow's timeline (avoid duplicate paired rows).
    rows = await FlowExecutionsService.list_for_flow(flow_id, str(user_id), limit=limit, offset=offset)
    return {"executions": rows, "limit": limit, "offset": offset}


class ScheduleFlowRequest(BaseModel):
    schedule_enabled: bool
    schedule_frequency: Optional[str] = None  # 'hourly', 'daily', 'monthly', 'yearly'
    time_of_day: Optional[str] = None  # 'HH:MM' format
    day_of_month: Optional[int] = None  # 1-31
    month: Optional[int] = None  # 1-12 for yearly
    timezone: Optional[str] = None  # Optional override, defaults to user timezone


@router.post("/flow/{flow_id}/schedule")
async def set_flow_schedule(
    flow_id: str,
    request: ScheduleFlowRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Enable/update schedule for a flow."""
    try:
        # Verify flow exists and belongs to user
        flow = await database.fetch_one(
            """
            SELECT id, user_id, parent_flow_id FROM migration_flows
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id)}
        )
        
        if not flow:
            raise HTTPException(status_code=404, detail="Flow not found")
        if dict(flow).get("parent_flow_id"):
            raise HTTPException(
                status_code=400,
                detail="Scheduling is only supported on the primary flow, not the bidirectional reverse leg.",
            )
        
        # Get user timezone if not provided
        user_result = await database.fetch_one(
            "SELECT timezone FROM users WHERE id = :user_id",
            {"user_id": str(user_id)}
        )
        # Always use current user timezone from database (timezone field is hidden in UI)
        # This ensures schedules always use the user's current timezone, even if they update their profile
        user_timezone = dict(user_result).get("timezone") if user_result else "America/Los_Angeles|Los Angeles, USA"
        
        # Build schedule_config JSON
        schedule_config = {}
        if request.time_of_day:
            schedule_config["time_of_day"] = request.time_of_day
        if request.day_of_month is not None:
            schedule_config["day_of_month"] = request.day_of_month
        if request.month is not None:
            schedule_config["month"] = request.month
        
        # Validate schedule configuration
        from services.schedule_calculator import validate_schedule_config
        
        if request.schedule_enabled:
            if not request.schedule_frequency:
                raise HTTPException(status_code=400, detail="schedule_frequency is required when schedule_enabled is true")
            
            is_valid, error_msg = validate_schedule_config(request.schedule_frequency, schedule_config)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_msg)
        
        # Calculate next_run_at if schedule is enabled
        next_run_at = None
        if request.schedule_enabled and request.schedule_frequency:
            from services.schedule_calculator import calculate_next_run
            from datetime import datetime
            # Explicitly pass current time to ensure accurate calculation
            next_run_at = calculate_next_run(
                frequency=request.schedule_frequency,
                schedule_config=schedule_config,
                timezone=user_timezone,
                current_time=datetime.utcnow()
            )
            print(f"📅 Calculated next_run_at for flow {flow_id}: {next_run_at} (UTC)")
            print(f"   Frequency: {request.schedule_frequency}, Config: {schedule_config}, Timezone: {user_timezone}")
        
        # Update database
        await database.execute(
            """
            UPDATE migration_flows
            SET schedule_enabled = :schedule_enabled,
                schedule_frequency = :schedule_frequency,
                schedule_config = :schedule_config,
                next_run_at = :next_run_at,
                schedule_timezone = :schedule_timezone,
                updated_at = NOW()
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {
                "flow_id": flow_id,
                "user_id": str(user_id),
                "schedule_enabled": request.schedule_enabled,
                "schedule_frequency": request.schedule_frequency,
                "schedule_config": json.dumps(schedule_config) if schedule_config else None,
                "next_run_at": next_run_at,
                "schedule_timezone": user_timezone
            }
        )
        
        return {
            "status": "success",
            "message": "Schedule updated successfully",
            "schedule": {
                "schedule_enabled": request.schedule_enabled,
                "schedule_frequency": request.schedule_frequency,
                "schedule_config": schedule_config,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
                "schedule_timezone": user_timezone
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error setting flow schedule: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to set schedule: {str(e)}")


@router.delete("/flow/{flow_id}/schedule")
async def delete_flow_schedule(
    flow_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """Disable schedule for a flow."""
    try:
        # Verify flow exists and belongs to user
        flow = await database.fetch_one(
            """
            SELECT id FROM migration_flows
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id)}
        )
        
        if not flow:
            raise HTTPException(status_code=404, detail="Flow not found")
        
        # Disable schedule
        await database.execute(
            """
            UPDATE migration_flows
            SET schedule_enabled = FALSE,
                schedule_frequency = NULL,
                schedule_config = NULL,
                next_run_at = NULL,
                updated_at = NOW()
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id)}
        )
        
        return {
            "status": "success",
            "message": "Schedule disabled successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting flow schedule: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete schedule: {str(e)}")


@router.get("/flow/{flow_id}/schedule")
async def get_flow_schedule(
    flow_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """Get schedule configuration for a flow."""
    try:
        # First verify flow exists
        flow_exists = await database.fetch_one(
            """
            SELECT id FROM migration_flows
            WHERE id = :flow_id AND user_id = :user_id
            """,
            {"flow_id": flow_id, "user_id": str(user_id)}
        )
        
        if not flow_exists:
            raise HTTPException(status_code=404, detail="Flow not found")
        
        # Get schedule columns (they might not exist if migration hasn't run)
        try:
            flow = await database.fetch_one(
                """
                SELECT schedule_enabled, schedule_frequency, schedule_config, 
                       next_run_at, last_run_at, schedule_timezone
                FROM migration_flows
                WHERE id = :flow_id AND user_id = :user_id
                """,
                {"flow_id": flow_id, "user_id": str(user_id)}
            )
        except Exception as db_error:
            # If columns don't exist, return empty schedule
            print(f"⚠️ Schedule columns might not exist yet: {db_error}")
            return {
                "status": "success",
                "schedule": {
                    "schedule_enabled": False,
                    "schedule_frequency": None,
                    "schedule_config": {},
                    "next_run_at": None,
                    "last_run_at": None,
                    "schedule_timezone": None
                }
            }
        
        # Convert Record to dict for easier handling (databases library returns Record objects)
        flow_dict = dict(flow) if flow else {}
        
        schedule_config = flow_dict.get("schedule_config")
        
        # Handle JSONB - PostgreSQL JSONB returns as dict, but could be string or None
        if schedule_config is None:
            schedule_config = {}
        elif isinstance(schedule_config, str):
            try:
                schedule_config = json.loads(schedule_config)
            except (json.JSONDecodeError, TypeError):
                schedule_config = {}
        # If it's already a dict (from JSONB), use it as-is
        
        # Handle datetime serialization
        next_run_at = flow_dict.get("next_run_at")
        last_run_at = flow_dict.get("last_run_at")
        
        return {
            "status": "success",
            "schedule": {
                "schedule_enabled": flow_dict.get("schedule_enabled", False),
                "schedule_frequency": flow_dict.get("schedule_frequency"),
                "schedule_config": schedule_config,
                "next_run_at": next_run_at.isoformat() if next_run_at else None,
                "last_run_at": last_run_at.isoformat() if last_run_at else None,
                "schedule_timezone": flow_dict.get("schedule_timezone")
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting flow schedule: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Failed to get schedule: {str(e)}")
