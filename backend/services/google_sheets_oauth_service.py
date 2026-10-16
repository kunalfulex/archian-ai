import httpx
import os
from typing import Optional, Dict, Any
from urllib.parse import urlencode
from fastapi import HTTPException
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

class GoogleSheetsOAuthService:
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError("Missing Google OAuth environment variables")
    
    def get_authorization_url(
        self,
        state: str = None,
        *,
        login_hint: Optional[str] = None,
    ) -> str:
        """Generate Google Sheets OAuth authorization URL"""
        base_url = "https://accounts.google.com/o/oauth2/v2/auth"
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile https://www.googleapis.com/auth/drive.file",
            "access_type": "offline",
            # select_account: pick which Google identity; consent: refresh token on first link
            "prompt": "select_account consent",
        }

        if state:
            params["state"] = state
        if login_hint and str(login_hint).strip():
            params["login_hint"] = str(login_hint).strip()

        return f"{base_url}?{urlencode(params)}"
    
    async def exchange_code_for_tokens(self, authorization_code: str) -> Dict[str, Any]:
        """Exchange authorization code for access and refresh tokens"""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "code": authorization_code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=data)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Failed to exchange code for tokens: {response.text}"
                )
            
            return response.json()
    
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Get user information from Google using access token"""
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        
        headers = {"Authorization": f"Bearer {access_token}"}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(userinfo_url, headers=headers)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Failed to get user info from Google: {response.text}"
                )
            
            return response.json()
    
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh expired access token using refresh token"""
        token_url = "https://oauth2.googleapis.com/token"
        
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(token_url, data=data)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=400, 
                    detail=f"Failed to refresh access token: {response.text}"
                )
            
            return response.json()
    
    def calculate_expires_at(self, expires_in: int) -> datetime:
        """Calculate when the access token expires"""
        return datetime.utcnow() + timedelta(seconds=expires_in)
    
    async def verify_and_get_user_info(self, access_token: str) -> Dict[str, Any]:
        """Verify Google access token and extract user information"""
        try:
            # Use the access token to get user info from Google
            user_info = await self.get_user_info(access_token)
            
            # Extract required fields
            return {
                "google_id": user_info.get("sub"),  # Google's unique user ID
                "email": user_info.get("email"),
                "first_name": user_info.get("given_name"),
                "last_name": user_info.get("family_name"),
                "profile_picture": user_info.get("picture"),
                "email_verified": user_info.get("email_verified", False)
            }
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to verify Google access token: {str(e)}"
            )
