import httpx
import os
from typing import Optional, Dict, Any
from fastapi import HTTPException
from dotenv import load_dotenv

load_dotenv()

class GoogleOAuthService:
    def __init__(self):
        self.client_id = os.getenv("GOOGLE_CLIENT_ID")
        self.client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        self.redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
        
        if not all([self.client_id, self.client_secret, self.redirect_uri]):
            raise ValueError("Missing Google OAuth environment variables")
    
    def get_authorization_url(self, state: str = None) -> str:
        """Generate Google OAuth authorization URL"""
        base_url = "https://accounts.google.com/o/oauth2/v2/auth"
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "consent"
        }
        
        if state:
            params["state"] = state
            
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{base_url}?{query_string}"
    
    async def exchange_code_for_tokens(self, authorization_code: str) -> Dict[str, Any]:
        """Exchange authorization code for access and ID tokens"""
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
    
    async def verify_id_token(self, access_token: str) -> Dict[str, Any]:
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
