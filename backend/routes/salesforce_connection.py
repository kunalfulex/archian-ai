"""User Salesforce connection status endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from db.connection import database
from routes.auth import get_current_user

router = APIRouter()


@router.get("/user-connection")
async def get_user_connection(user_id: UUID = Depends(get_current_user)):
    """Get the user's Salesforce connection information"""
    try:
        # Get the user's Salesforce connection details from salesforce_tokens table
        query = """
        SELECT 
            instance_url,
            access_token,
            refresh_token,
            issued_at
        FROM salesforce_tokens 
        WHERE user_id = :user_id 
        ORDER BY issued_at DESC
        LIMIT 1
        """
        
        connection = await database.fetch_one(query, values={"user_id": str(user_id)})
        
        if not connection:
            return JSONResponse(
                content={
                    "status": "error",
                    "message": "No Salesforce connection found",
                    "connection": None
                },
                status_code=404
            )
        
        return JSONResponse(
            content={
                "status": "success",
                "message": "Salesforce connection found",
                "connection": {
                    "instance_url": connection["instance_url"]
                }
            }
        )
        
    except Exception as e:
        print(f"❌ Error getting user connection: {e}")
        raise HTTPException(status_code=500, detail=f"Error getting user connection: {str(e)}")


@router.get("/salesforce/connection-status")
async def get_salesforce_connection_status(
    user_id: UUID = Depends(get_current_user)
):
    """Check if user has an active Salesforce connection"""
    try:
        # Check if user has any Salesforce tokens
        query = "SELECT COUNT(*) as token_count FROM salesforce_tokens WHERE user_id = :user_id"
        result = await database.fetch_one(query, values={"user_id": str(user_id)})
        
        has_connection = result["token_count"] > 0 if result else False
        
        return JSONResponse(
            content={
                "status": "success",
                "has_salesforce_connection": has_connection
            }
        )
        
    except Exception as e:
        print(f"❌ Error checking Salesforce connection status: {e}")
        raise HTTPException(status_code=500, detail=f"Error checking Salesforce connection status: {str(e)}")
