"""
Google Sheets Authentication Utilities

Helper functions for managing Google Sheets OAuth tokens, including
automatic token refresh when tokens expire.
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone
from db.connection import database


async def get_valid_google_sheets_token(
    user_id: str,
    google_sheets_connection_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Get valid Google Sheets access token, refreshing if expired.
    
    This function ensures that all Google Sheets API calls use a valid,
    non-expired access token. If the token is expired, it automatically
    refreshes it using the refresh token and updates the database.
    
    Args:
        user_id: User ID to get token for
        google_sheets_connection_id: Optional specific connection ID.
            If not provided, uses the most recent active connection.
    
    Returns:
        Dictionary with token information:
        {
            "access_token": str,
            "refresh_token": str,
            "expires_at": datetime,
            "connection_id": str,
            "google_account_email": str,
            "google_account_name": str (optional)
        }
        Returns None if connection not found.
    
    Raises:
        Exception: If token refresh fails (refresh token expired, etc.)
    """
    # Build query based on whether connection_id is provided
    if google_sheets_connection_id:
        query = """
            SELECT id, access_token, refresh_token, expires_at, google_account_email, google_account_name
            FROM google_sheets_connections 
            WHERE id = :connection_id AND user_id = :user_id AND is_active = true
        """
        params = {
            "connection_id": google_sheets_connection_id,
            "user_id": user_id
        }
    else:
        query = """
            SELECT id, access_token, refresh_token, expires_at, google_account_email, google_account_name
            FROM google_sheets_connections 
            WHERE user_id = :user_id AND is_active = true
            ORDER BY created_at DESC 
            LIMIT 1
        """
        params = {"user_id": user_id}
    
    # Fetch connection from database
    connection = await database.fetch_one(query, params)
    
    if not connection:
        return None
    
    # Extract token information
    access_token = connection["access_token"]
    refresh_token = connection["refresh_token"]
    expires_at = connection["expires_at"]
    connection_id = connection["id"]
    google_account_email = connection["google_account_email"]
    google_account_name = connection["google_account_name"]
    
    # Check if token is expired and refresh if needed
    if expires_at:
        now = datetime.now(timezone.utc)
        # Handle timezone-aware and naive datetimes
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        
        if expires_at <= now:
            print(f"🔄 Google Sheets access token expired, refreshing...")
            try:
                from services.google_sheets_oauth_service import GoogleSheetsOAuthService
                
                oauth_service = GoogleSheetsOAuthService()
                token_response = await oauth_service.refresh_access_token(refresh_token)
                
                # Update token in database
                new_access_token = token_response.get("access_token")
                expires_in = token_response.get("expires_in", 3600)
                new_expires_at = oauth_service.calculate_expires_at(expires_in)
                
                await database.execute(
                    """
                    UPDATE google_sheets_connections
                    SET access_token = :access_token, expires_at = :expires_at, updated_at = NOW()
                    WHERE id = :connection_id
                    """,
                    {
                        "access_token": new_access_token,
                        "expires_at": new_expires_at,
                        "connection_id": connection_id
                    }
                )
                
                access_token = new_access_token
                expires_at = new_expires_at
                print(f"✅ Google Sheets token refreshed successfully")
            except Exception as refresh_error:
                error_message = str(refresh_error)
                print(f"❌ Error refreshing Google Sheets token: {error_message}")
                
                # Provide user-friendly error messages
                if "invalid_grant" in error_message.lower() or "invalid_token" in error_message.lower():
                    raise Exception(
                        "Google Sheets refresh token expired. Please reconnect your Google Sheets account from the Connections tab."
                    )
                elif "invalid_client" in error_message.lower():
                    raise Exception(
                        "Invalid Google OAuth credentials. Please check your Google OAuth configuration and reconnect from the Connections tab."
                    )
                else:
                    raise Exception(
                        f"Failed to refresh Google Sheets token. Please reconnect your Google Sheets account from the Connections tab. Error: {error_message}"
                    )
    
    # Return token information
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "id": connection_id,  # For backward compatibility with existing code
        "connection_id": str(connection_id),  # Also include as connection_id
        "google_account_email": google_account_email,
        "google_account_name": google_account_name
    }


async def get_token_for_detection(
    user_id: str,
    detection: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Resolve OAuth tokens for a detection result.
    Prefers connection linked to spreadsheet_id, then explicit connection_id,
    then newest active connection.
    """
    from services.google_sheets_connection_service import resolve_connection_for_spreadsheet

    uid = str(user_id)
    if detection and detection.get("error") == "ambiguous_spreadsheet_name":
        return None

    if detection:
        sid = detection.get("spreadsheet_id")
        cid = detection.get("google_sheets_connection_id")
        if sid:
            conn = await resolve_connection_for_spreadsheet(uid, str(sid))
            if conn:
                return conn
        if cid:
            conn = await get_valid_google_sheets_token(uid, str(cid))
            if conn:
                return conn

    return await get_valid_google_sheets_token(uid)


async def user_has_google_sheets_connection(user_id: str) -> bool:
    from services.google_sheets_connection_service import user_has_active_connection

    return await user_has_active_connection(str(user_id))

