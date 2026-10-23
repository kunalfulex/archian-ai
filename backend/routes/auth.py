# src/routes/auth.py

from fastapi import APIRouter, HTTPException, Depends, Body, Header, Request, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from datetime import datetime
import random
import string
import httpx
from db.connection import database  # uses your existing db instance
from utils.email_utils import send_verification_email, send_forgot_password_email, send_welcome_email
from services.user_management_service import UserManagementService
import jwt  # PyJWT — used only for exception types in this module
import bcrypt
from dotenv import load_dotenv
import os
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4
from utils.auth_rate_limit import check_auth_rate_limit
from utils.auth_cookies import (
    attach_pre_mfa_auth,
    attach_session_auth,
    clear_all_auth_cookies,
    read_pre_mfa_token,
    read_session_token,
)
from utils.auth_tokens import (
    JWT_EXPIRY_SECONDS,
    assert_pre_mfa_token,
    assert_session_token,
    create_pre_mfa_token,
    create_session_token,
    decode_token,
)
load_dotenv()

router = APIRouter()


def _session_auth_response(content: Dict[str, Any], session_token: str) -> JSONResponse:
    """Issue httpOnly session cookie + CSRF cookie; JWT is not returned in JSON."""
    response = JSONResponse(content={**content, "authenticated": True})
    attach_session_auth(response, session_token)
    return response


def _pre_mfa_auth_response(content: Dict[str, Any], pre_mfa_token: str) -> JSONResponse:
    response = JSONResponse(content={**content, "token_type": "pre_mfa"})
    attach_pre_mfa_auth(response, pre_mfa_token)
    return response

class SendCodeRequest(BaseModel):
    email: EmailStr

@router.post("/api/send-code")
async def send_code(request: SendCodeRequest, http_request: Request):
    email = request.email.lower()
    client_ip = http_request.client.host if http_request.client else "unknown"
    check_auth_rate_limit(client_ip, "send-code")

    code = ''.join(random.choices(string.digits, k=6))
    now = datetime.utcnow()

    try:
        user = await database.fetch_one("SELECT id, is_active FROM users WHERE email = :email AND is_deleted = FALSE", values={"email": email})

        if not user:
            support = (os.getenv("SUPPORT_EMAIL") or "").strip()
            detail = "This email is not authorized to access Archian AI."
            if support:
                detail += f" Please contact {support} to request access."
            raise HTTPException(status_code=403, detail=detail)

        if not hasattr(user, 'is_active') or not user.is_active:
            raise HTTPException(status_code=403, detail="This account has been disabled. Please contact support.")

        # User exists and is active → send code
        await database.execute(
            """
            UPDATE users 
            SET verification_code = :code, code_generated_at = :now 
            WHERE email = :email
            """,
            values={"code": code, "now": now, "email": email}
        )
        await send_verification_email(email, code)
        return {"success": True, "message": "Verification code sent."}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending code: {str(e)}")


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str


@router.post("/api/verify-code")
async def verify_code(
    request: VerifyCodeRequest,
    http_request: Request,
    authorization: str = Header(None),
):
    email = request.email.lower()
    code = request.code.strip()

    temp_token = read_pre_mfa_token(http_request, authorization)
    if not temp_token:
        raise HTTPException(status_code=401, detail="Missing temporary token. Please login again.")

    try:
        payload = decode_token(temp_token)
        assert_pre_mfa_token(payload)
        if payload.get("email") != email:
            raise HTTPException(status_code=401, detail="Token email mismatch. Please login again.")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Temporary token expired. Please login again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid temporary token. Please login again.")
    except HTTPException:
        raise

    user = await database.fetch_one(
        "SELECT id, verification_code, code_generated_at FROM users WHERE email = :email AND is_deleted = FALSE",
        values={"email": email}
    )

    if not user or not hasattr(user, 'verification_code') or user.verification_code != code:
        raise HTTPException(status_code=401, detail="Invalid code.")

    code_time = user.code_generated_at
    if (datetime.utcnow() - code_time).total_seconds() > 600:
        raise HTTPException(status_code=401, detail="Code expired.")

    await database.execute(
        "UPDATE users SET last_login_at = :now WHERE email = :email",
        values={"now": datetime.utcnow(), "email": email}
    )

    token = create_session_token(email, str(user.id))
    return _session_auth_response(
        {"success": True, "user_id": str(user.id), "email": email},
        token,
    )



@router.get("/api/verify-token")
async def verify_token(http_request: Request, authorization: str = Header(None)):
    """Probe session (cookie or Bearer). Always 200 — use authenticated flag, not 401."""
    token = read_session_token(http_request, authorization)
    if not token:
        return {"success": True, "authenticated": False}

    try:
        payload = decode_token(token)
        assert_session_token(payload)
        email = payload.get("email")
        user_id = payload.get("user_id")

        if not email or not user_id:
            return {"success": True, "authenticated": False}

        return {
            "success": True,
            "authenticated": True,
            "user_id": user_id,
            "email": email,
        }

    except jwt.ExpiredSignatureError:
        return {"success": True, "authenticated": False}
    except jwt.InvalidTokenError:
        return {"success": True, "authenticated": False}


@router.post("/api/logout")
async def logout():
    response = JSONResponse(content={"success": True})
    clear_all_auth_cookies(response)
    return response


async def get_current_user(http_request: Request, authorization: str = Header(None)) -> UUID:
    """
    Returns user_id from httpOnly session cookie (preferred) or Bearer token (scripts).
    Pre-MFA tokens are rejected.
    """
    token = read_session_token(http_request, authorization)
    if not token:
        raise HTTPException(status_code=401, detail="Missing or invalid token")

    try:
        payload = decode_token(token)
        assert_session_token(payload)

        user_id = UUID(payload.get("user_id"))
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token: user_id missing")

        user = await database.fetch_one(
            "SELECT id, is_active FROM users WHERE id = :id AND is_deleted = FALSE",
            values={"id": user_id},
        )
        if not user:
            raise HTTPException(status_code=401, detail="User not found or account deleted")
        if not getattr(user, "is_active", True):
            raise HTTPException(status_code=403, detail="Account is disabled.")

        return user_id

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token")


async def get_super_admin_user(user_id: UUID = Depends(get_current_user)) -> UUID:
    """
    Requires authenticated user and is_super_admin = true. Use for admin-only endpoints.
    """
    row = await database.fetch_one(
        "SELECT is_super_admin FROM users WHERE id = :id AND is_deleted = FALSE",
        values={"id": user_id},
    )
    if not row or not getattr(row, "is_super_admin", False):
        raise HTTPException(status_code=403, detail="Access denied. Super admin only.")
    return user_id


class ConnectedAppRequest(BaseModel):
    id: Optional[int] = None
    client_id: Optional[str] = None  # Required for manual app; omit for SSO token-only updates
    client_secret: Optional[str] = None
    environment_type: str  # production or sandbox
    connection_name: Optional[str] = None  # User-friendly name for the org (legacy, on connected app)
    org_display_name: Optional[str] = None  # Org name displayed to user; updates salesforce_tokens.org_display_name
    org_id: Optional[str] = None  # When provided, update this specific token's org_display_name
    is_default: Optional[bool] = False

@router.post("/api/salesforce-connected-app")
async def save_connected_app(request: ConnectedAppRequest, user_id: UUID = Depends(get_current_user)):
    try:
        # SSO token-only update: only org_id provided, update token metadata (org_display_name, environment_type, is_default)
        if request.org_id and (request.client_id is None or request.client_secret is None):
            is_default = bool(request.is_default)
            if is_default:
                await database.execute(
                    """
                    UPDATE salesforce_tokens SET is_default = FALSE WHERE user_id = :user_id
                    """,
                    values={"user_id": user_id},
                )
            update_fields = ["environment_type = :environment_type", "is_default = :is_default"]
            update_values = {"user_id": user_id, "org_id": request.org_id, "environment_type": request.environment_type, "is_default": is_default}
            if request.org_display_name is not None and str(request.org_display_name or "").strip():
                update_fields.append("org_display_name = :org_display_name")
                update_values["org_display_name"] = request.org_display_name.strip()
            await database.execute(
                f"""
                UPDATE salesforce_tokens
                SET {", ".join(update_fields)}
                WHERE user_id = :user_id AND org_id = :org_id
                """,
                values=update_values,
            )
            return {"success": True, "id": request.org_id}

        if not request.client_id or not request.client_secret:
            raise HTTPException(status_code=400, detail="client_id and client_secret are required for manual Connected App setup.")

        # Generate default connection name if not provided
        connection_name = request.connection_name
        if not connection_name:
            connection_name = "Production Org" if request.environment_type == "production" else "Sandbox Org"

        is_default = bool(request.is_default)

        if is_default and request.org_id:
            # Clear existing defaults in salesforce_tokens before assigning the new default
            await database.execute(
                """
                UPDATE salesforce_tokens SET is_default = FALSE WHERE user_id = :user_id
                """,
                values={"user_id": user_id},
            )
        # is_default lives on salesforce_tokens; clearing app-level default not needed (token-centric)

        base_values = {
            "client_id": request.client_id,
            "client_secret": request.client_secret,
            "environment_type": request.environment_type,
            "connection_name": connection_name,
            "updated_at": datetime.utcnow(),
            "user_id": user_id,
            "is_default": is_default,
        }

        if request.id is not None:
            # Ensure the connected app belongs to the user
            existing = await database.fetch_one(
                """
                SELECT id FROM salesforce_connected_apps 
                WHERE id = :id AND user_id = :user_id
                """,
                values={"id": request.id, "user_id": user_id}
            )
            if not existing:
                raise HTTPException(status_code=404, detail="Connected app not found for this user")

            # Token-centric: only update credentials. is_default lives on salesforce_tokens.
            await database.execute(
                """
                UPDATE salesforce_connected_apps 
                SET client_id = :client_id, 
                    client_secret = :client_secret, 
                    updated_at = :updated_at
                WHERE id = :id AND user_id = :user_id
                """,
                values={**base_values, "id": request.id}
            )

            connected_app_id = request.id
        else:
            # Insert
            # Token-centric: only store credentials. is_default lives on salesforce_tokens.
            insert_row = await database.fetch_one(
                """
                INSERT INTO salesforce_connected_apps (user_id, client_id, client_secret, redirect_uri, created_at, updated_at, uuid_id)
                VALUES (:user_id, :client_id, :client_secret, :redirect_uri, :created_at, :updated_at, gen_random_uuid())
                RETURNING id
                """,
                values={
                    "user_id": user_id,
                    "client_id": request.client_id,
                    "client_secret": request.client_secret,
                    "redirect_uri": f"{os.getenv('FRONTEND_URL')}/oauth/callback",
                    "created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            )
            connected_app_id = insert_row["id"] if insert_row else None

        # Update salesforce_tokens (org_display_name, environment_type, is_default) when org_id provided
        if connected_app_id and request.org_id:
            update_fields = ["environment_type = :environment_type", "is_default = :is_default"]
            update_values = {
                "user_id": user_id,
                "connected_app_id": connected_app_id,
                "org_id": request.org_id,
                "environment_type": request.environment_type,
                "is_default": is_default,
            }
            if request.org_display_name is not None and request.org_display_name.strip():
                update_fields.append("org_display_name = :org_display_name")
                update_values["org_display_name"] = request.org_display_name.strip()

            await database.execute(
                f"""
                UPDATE salesforce_tokens
                SET {", ".join(update_fields)}
                WHERE user_id = :user_id AND connected_app_id = :connected_app_id AND org_id = :org_id
                """,
                values=update_values,
            )
        elif request.org_display_name is not None and request.org_display_name.strip() and connected_app_id:
            # No org_id: update org_display_name for all tokens of this app
            org_display_name = request.org_display_name.strip()
            await database.execute(
                """
                UPDATE salesforce_tokens
                SET org_display_name = :org_display_name
                WHERE user_id = :user_id AND connected_app_id = :connected_app_id
                """,
                values={
                    "user_id": user_id,
                    "connected_app_id": connected_app_id,
                    "org_display_name": org_display_name,
                },
            )

        return {"success": True, "id": connected_app_id}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error saving connected app: {str(e)}")


@router.get("/api/connected-app")
async def get_connected_app(user_id: UUID = Depends(get_current_user)):
    # Token-centric: salesforce_tokens is the source of truth. LEFT JOIN salesforce_connected_apps
    # only for manual connections (client_id/client_secret). SSO connections don't need app details;
    # we use token_id as id for SSO to avoid duplicate cards (multiple SSO orgs share same Archian app).
    token_results = await database.fetch_all(
        """
        SELECT st.id AS token_id, st.org_id, st.org_display_name, st.environment_type, st.is_default,
               st.connected_app_id, st.is_sso_connection, st.issued_at,
               sca.client_id, sca.client_secret, sca.created_at, sca.updated_at
        FROM salesforce_tokens st
        LEFT JOIN salesforce_connected_apps sca ON st.connected_app_id = sca.id AND sca.user_id = st.user_id
        WHERE st.user_id = :user_id
        ORDER BY st.is_default DESC NULLS LAST, st.issued_at DESC
        """,
        values={"user_id": user_id}
    )

    connected_apps = []

    for record in token_results:
        rec = dict(record)
        token_id = rec.get("token_id")
        app_id = rec.get("connected_app_id")
        is_sso = bool(rec.get("is_sso_connection"))
        connection_name = rec.get("org_display_name") or rec.get("org_id") or "Salesforce"
        # SSO: use token_id as id (avoids duplicates - multiple SSO orgs share same Archian app_id).
        # Manual: use connected_app_id (OAuth/save compat).
        row_id = str(token_id) if is_sso and token_id else (app_id if app_id else str(token_id) if token_id else None)
        if row_id:
            connected_apps.append({
                "id": row_id if isinstance(row_id, int) else row_id,
                "token_id": str(token_id) if token_id else None,
                "connected_app_id": app_id,
                "org_id": rec.get("org_id"),
                "client_id": rec.get("client_id"),
                "client_secret": rec.get("client_secret"),
                "environment_type": rec.get("environment_type") or "production",
                "connection_name": connection_name,
                "created_at": rec.get("created_at").isoformat() if rec.get("created_at") else None,
                "updated_at": rec.get("updated_at").isoformat() if rec.get("updated_at") else None,
                "is_default": rec.get("is_default"),
                "is_sso_connection": bool(rec.get("is_sso_connection")),
            })

    # Orphan apps: manual Connected Apps with no tokens (user added credentials but hasn't OAuth'd)
    # is_default lives on salesforce_tokens; orphan apps have no tokens, so default to False
    orphan_apps = await database.fetch_all(
        """
        SELECT sca.id, sca.client_id, sca.client_secret, sca.created_at, sca.updated_at
        FROM salesforce_connected_apps sca
        WHERE sca.user_id = :user_id
          AND sca.id NOT IN (SELECT connected_app_id FROM salesforce_tokens WHERE user_id = :user_id AND connected_app_id IS NOT NULL)
        ORDER BY sca.created_at ASC
        """,
        values={"user_id": user_id}
    )

    for record in orphan_apps:
        rec = dict(record)
        connected_apps.append({
            "id": rec["id"],
            "token_id": None,
            "connected_app_id": rec["id"],
            "client_id": rec["client_id"],
            "client_secret": rec["client_secret"],
            "environment_type": "production",
            "connection_name": "Salesforce (not connected)",
            "created_at": rec.get("created_at").isoformat() if rec.get("created_at") else None,
            "updated_at": rec.get("updated_at").isoformat() if rec.get("updated_at") else None,
            "is_default": False,
            "is_sso_connection": False,
        })

    return {"connected_apps": connected_apps}


# New signup and login functionality
TURNSTILE_SECRET_KEY = os.getenv("TURNSTILE_SECRET_KEY")
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    first_name: str
    last_name: str
    timezone: str = "America/Los_Angeles|Los Angeles, USA"  # Default timezone with fallback (IANA format)
    turnstile_token: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    turnstile_token: Optional[str] = None


async def _enforce_turnstile(turnstile_token: Optional[str], http_request: Request) -> None:
    if not TURNSTILE_SECRET_KEY:
        return
    if not turnstile_token or not turnstile_token.strip():
        raise HTTPException(
            status_code=400,
            detail="Verification is required. Please complete the challenge and try again.",
        )
    remote_ip = http_request.client.host if http_request.client else None
    if not await _verify_turnstile_token(turnstile_token.strip(), remote_ip):
        raise HTTPException(status_code=400, detail="Verification failed. Please try again.")

async def _verify_turnstile_token(token: str, remote_ip: Optional[str] = None) -> bool:
    """Verify Turnstile token with Cloudflare. Returns True if valid."""
    if not TURNSTILE_SECRET_KEY:
        return True  # Skip verification when not configured (e.g. local dev)
    payload = {"secret": TURNSTILE_SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(TURNSTILE_VERIFY_URL, data=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("success") is True
    except Exception:
        return False


@router.post("/api/signup")
async def signup(request: SignupRequest, http_request: Request):
    email = request.email.lower()

    if TURNSTILE_SECRET_KEY:
        await _enforce_turnstile(request.turnstile_token, http_request)
    
    try:
        # Check if user already exists (including soft-deleted)
        existing_user = await database.fetch_one(
            "SELECT id, is_deleted FROM users WHERE email = :email",
            values={"email": email}
        )
        
        if existing_user:
            if hasattr(existing_user, 'is_deleted') and existing_user.is_deleted:
                # User exists but is soft-deleted, check if within reactivation window
                can_reactivate, message, user_id = await UserManagementService.check_user_can_reactivate(email)
                if can_reactivate:
                    # Reactivate the account
                    success, reactivate_message = await UserManagementService.reactivate_user(user_id)
                    if success:
                        return {"success": True, "message": "Account reactivated successfully", "user_id": user_id}
                    else:
                        raise HTTPException(status_code=400, detail=reactivate_message)
                else:
                    raise HTTPException(status_code=400, detail=message)
            else:
                raise HTTPException(status_code=400, detail="User with this email already exists.")
        
        # Hash the password
        password_hash = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        
        # Create new user
        user_id = await database.execute(
            """
            INSERT INTO users (email, password_hash, first_name, last_name, timezone, is_active, is_verified, created_at, updated_at)
            VALUES (:email, :password_hash, :first_name, :last_name, :timezone, :is_active, :is_verified, :created_at, :updated_at)
            RETURNING id
            """,
            values={
                "email": email,
                "password_hash": password_hash,
                "first_name": request.first_name,
                "last_name": request.last_name,
                "timezone": request.timezone,
                "is_active": True,
                "is_verified": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
        )
        
        # Create default free plan for new user
        now = datetime.utcnow()
        try:
            await database.execute(
                """
                INSERT INTO user_plans (
                    user_id, plan_type, status, current_period_start, 
                    current_period_end, created_at, updated_at
                ) VALUES (
                    :user_id, 'free', 'active', :now, :now, :now, :now
                )
                """,
                values={"user_id": str(user_id), "now": now}
            )
            print(f"✅ Free plan created for user {user_id}")
            # One-time free signup credits (500); no monthly refill
            try:
                from services.entitlements import get_entitlements
                await get_entitlements().grant_free_signup(str(user_id))
                print(f"✅ Free signup credits granted for user {user_id}")
            except Exception as cred_e:
                print(f"⚠️ Failed to grant free signup credits: {cred_e}")
        except Exception as e:
            print(f"❌ Failed to create free plan for user {user_id}: {e}")
            # Don't fail signup if plan creation fails, but log the error
        
        # Send welcome email to new user
        try:
            await send_welcome_email(email, request.first_name)
            print(f"✅ Welcome email sent to {email}")
        except Exception as e:
            print(f"❌ Failed to send welcome email to {email}: {e}")
            # Don't fail signup if email fails
        
        return {"success": True, "message": "User created successfully", "user_id": str(user_id)}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating user: {str(e)}")

@router.post("/api/login")
async def login(request: LoginRequest, http_request: Request):
    email = request.email.lower()
    client_ip = http_request.client.host if http_request.client else "unknown"
    check_auth_rate_limit(client_ip, "login")

    await _enforce_turnstile(request.turnstile_token, http_request)
    
    try:
        # Check if user exists and get password hash
        user = await database.fetch_one(
            "SELECT id, password_hash, is_active FROM users WHERE email = :email AND is_deleted = FALSE",
            values={"email": email}
        )
        
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        
        if not hasattr(user, 'is_active') or not user.is_active:
            raise HTTPException(status_code=403, detail="Account is disabled.")
        
        # Verify password
        if not bcrypt.checkpw(request.password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        
        # Update last login time
        await database.execute(
            "UPDATE users SET last_login_at = :now WHERE email = :email",
            values={"now": datetime.utcnow(), "email": email}
        )
        
        # Log successful login attempt
        try:
            # Get client IP address from request
            client_ip = http_request.client.host if hasattr(http_request, 'client') and http_request.client else "unknown"
            
            # Get user agent from headers
            user_agent = http_request.headers.get("user-agent", "unknown")
            
            # Insert into login history
            await database.execute(
                """
                INSERT INTO user_login_history (user_id, ip_address, user_agent, login_status, uuid_id)
                VALUES (:user_id, :ip_address, :user_agent, 'success', gen_random_uuid())
                """,
                values={
                    "user_id": user.id,
                    "ip_address": client_ip,
                    "user_agent": user_agent
                }
            )
        except Exception as e:
            print(f"❌ Error logging login history: {e}")
            # Don't fail login if logging fails
        
        # Pre-MFA token: cannot access protected APIs until verify-code completes
        token = create_pre_mfa_token(email, str(user.id))

        return _pre_mfa_auth_response(
            {
                "success": True,
                "message": "Login successful",
                "user_id": str(user.id),
            },
            token,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error during login: {str(e)}")



@router.get("/api/salesforce-connection")
async def get_salesforce_connection(user_id: UUID = Depends(get_current_user)):
    records = await database.fetch_all(
        """
        SELECT 
            id,
            instance_url,
            issued_at,
            salesforce_user_email,
            org_id,
            connected_app_id,
            org_display_name,
            COALESCE(is_sso_connection, false) AS is_sso_connection,
            environment_type,
            is_default
        FROM salesforce_tokens
        WHERE user_id = :user_id
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        """,
        values={"user_id": user_id},
    )

    if not records:
        return JSONResponse(content={"connected": False, "connections": []})

    connections: List[Dict[str, Any]] = []
    for row in records:
        row_dict = dict(row)
        issued_at = row_dict.get("issued_at")
        connections.append(
            {
                "token_id": str(row_dict["id"]) if row_dict.get("id") else None,
                "connected_app_id": row_dict.get("connected_app_id"),
                "connection_name": row_dict.get("org_display_name"),
                "org_display_name": row_dict.get("org_display_name"),
                "environment_type": row_dict.get("environment_type"),
                "is_default": bool(row_dict.get("is_default", False)),
                "instance_url": row_dict.get("instance_url"),
                "salesforce_username": row_dict.get("salesforce_user_email"),
                "org_id": row_dict.get("org_id"),
                "issued_at": issued_at.isoformat() if issued_at else None,
                "is_sso_connection": bool(row_dict.get("is_sso_connection", False)),
            }
        )

    primary_connection = connections[0]

    return JSONResponse(
        content={
            "connected": True,
            "instance_url": primary_connection.get("instance_url"),
            "salesforce_username": primary_connection.get("salesforce_username"),
            "issued_at": primary_connection.get("issued_at"),
            "connections": connections,
        }
    )


@router.delete("/api/salesforce-connection/{identifier}")
async def delete_salesforce_connection(identifier: str, user_id: UUID = Depends(get_current_user)):
    """Delete a single Salesforce connection (token) by org_id or token_id (UUID). For SSO connections."""
    try:
        token_row = await database.fetch_one(
            """
            SELECT org_id, connected_app_id FROM salesforce_tokens
            WHERE user_id = :user_id AND (org_id = :identifier OR id::text = :identifier)
            LIMIT 1
            """,
            values={"user_id": user_id, "identifier": identifier},
        )
        if not token_row:
            raise HTTPException(status_code=404, detail="Salesforce connection not found.")

        org_id = token_row["org_id"]
        connected_app_id = token_row["connected_app_id"]

        for tbl in ["salesforce_object_fields", "salesforce_objects"]:
            try:
                await database.execute(
                    f"DELETE FROM {tbl} WHERE user_id = :user_id AND org_id = :org_id",
                    values={"user_id": user_id, "org_id": org_id},
                )
            except Exception as e:
                print(f"⚠️ Warning: Error deleting from {tbl} for org {org_id}: {e}")

        await database.execute(
            """
            DELETE FROM salesforce_tokens
            WHERE user_id = :user_id AND org_id = :org_id
            """,
            values={"user_id": user_id, "org_id": org_id},
        )

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting connection: {str(e)}")


@router.delete("/api/salesforce-connected-app/{app_id}")
async def delete_salesforce_connected_app(app_id: int, user_id: UUID = Depends(get_current_user)):
    try:
        existing_app = await database.fetch_one(
            """
            SELECT id FROM salesforce_connected_apps
            WHERE id = :app_id AND user_id = :user_id
            """,
            values={"app_id": app_id, "user_id": user_id},
        )

        if not existing_app:
            raise HTTPException(status_code=404, detail="Salesforce connected app not found.")

        # Get all org_ids associated with this connected app before deleting tokens
        org_ids_result = await database.fetch_all(
            """
            SELECT DISTINCT org_id FROM salesforce_tokens
            WHERE user_id = :user_id AND connected_app_id = :app_id AND org_id IS NOT NULL
            """,
            values={"user_id": user_id, "app_id": app_id},
        )
        
        org_ids = [row["org_id"] for row in org_ids_result if row["org_id"] is not None]
        
        # Delete Salesforce object fields for all orgs associated with this connected app
        if org_ids:
            for org_id in org_ids:
                try:
                    await database.execute(
                        """
                        DELETE FROM salesforce_object_fields
                        WHERE user_id = :user_id AND org_id = :org_id
                        """,
                        values={"user_id": user_id, "org_id": org_id},
                    )
                    print(f"✅ Deleted Salesforce object fields for org {org_id}")
                except Exception as e:
                    print(f"⚠️ Warning: Error deleting Salesforce object fields for org {org_id}: {e}")
            
            # Delete Salesforce objects for all orgs associated with this connected app
            for org_id in org_ids:
                try:
                    await database.execute(
                        """
                        DELETE FROM salesforce_objects
                        WHERE user_id = :user_id AND org_id = :org_id
                        """,
                        values={"user_id": user_id, "org_id": org_id},
                    )
                    print(f"✅ Deleted Salesforce objects for org {org_id}")
                except Exception as e:
                    print(f"⚠️ Warning: Error deleting Salesforce objects for org {org_id}: {e}")

        # Delete related Salesforce tokens tied to this connected app
        await database.execute(
            """
            DELETE FROM salesforce_tokens
            WHERE user_id = :user_id AND connected_app_id = :app_id
            """,
            values={"user_id": user_id, "app_id": app_id},
        )

        # Delete the connected app
        await database.execute(
            """
            DELETE FROM salesforce_connected_apps
            WHERE id = :app_id AND user_id = :user_id
            """,
            values={"app_id": app_id, "user_id": user_id},
        )

        # Ensure there is still a default token (is_default lives on salesforce_tokens)
        remaining_default = await database.fetch_one(
            """
            SELECT id FROM salesforce_tokens
            WHERE user_id = :user_id AND is_default = TRUE
            LIMIT 1
            """,
            values={"user_id": user_id},
        )

        if not remaining_default:
            fallback_token = await database.fetch_one(
                """
                SELECT id FROM salesforce_tokens
                WHERE user_id = :user_id
                ORDER BY issued_at DESC
                LIMIT 1
                """,
                values={"user_id": user_id},
            )
            if fallback_token:
                await database.execute(
                    """
                    UPDATE salesforce_tokens
                    SET is_default = TRUE
                    WHERE id = :token_id
                    """,
                    values={"token_id": fallback_token["id"]},
                )

        return {"success": True}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error deleting connected app: {str(e)}")


# Forgot Password Endpoints
class ForgotPasswordSendCodeRequest(BaseModel):
    email: EmailStr

@router.post("/api/forgot-password/send-code")
async def forgot_password_send_code(request: ForgotPasswordSendCodeRequest):
    email = request.email.lower()
    
    try:
        # Check if user exists
        user = await database.fetch_one(
            "SELECT id, is_active FROM users WHERE email = :email", 
            values={"email": email}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="Email not found.")
        
        if not hasattr(user, 'is_active') or not user.is_active:
            raise HTTPException(status_code=403, detail="Account is disabled.")
        
        # Generate verification code
        code = ''.join(random.choices(string.digits, k=6))
        now = datetime.utcnow()
        
        # Store verification code
        await database.execute(
            """
            UPDATE users 
            SET verification_code = :code, code_generated_at = :now 
            WHERE email = :email
            """,
            values={"code": code, "now": now, "email": email}
        )
        
        # Send verification email
        await send_forgot_password_email(email, code)
        
        return {"success": True, "message": "Verification code sent to your email."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error sending code: {str(e)}")


class ForgotPasswordVerifyCodeRequest(BaseModel):
    email: EmailStr
    verification_code: str

@router.post("/api/forgot-password/verify-code")
async def forgot_password_verify_code(request: ForgotPasswordVerifyCodeRequest):
    email = request.email.lower()
    code = request.verification_code.strip()
    
    try:
        # Check if user exists and verify code
        user = await database.fetch_one(
            "SELECT id, verification_code, code_generated_at FROM users WHERE email = :email AND is_deleted = FALSE",
            values={"email": email}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="Email not found.")
        
        if not hasattr(user, 'verification_code') or user.verification_code != code:
            raise HTTPException(status_code=401, detail="Invalid verification code.")
        
        # Check if code is expired (10 minutes)
        code_time = user.code_generated_at
        if (datetime.utcnow() - code_time).total_seconds() > 600:
            raise HTTPException(status_code=401, detail="Verification code expired.")
        
        return {"success": True, "message": "Code verified successfully."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error verifying code: {str(e)}")


class ForgotPasswordUpdateRequest(BaseModel):
    email: EmailStr
    verification_code: str
    new_password: str

@router.post("/api/forgot-password/update-password")
async def forgot_password_update_password(request: ForgotPasswordUpdateRequest):
    email = request.email.lower()
    code = request.verification_code.strip()
    new_password = request.new_password
    
    try:
        # Verify the code again before updating password
        user = await database.fetch_one(
            "SELECT id, verification_code, code_generated_at FROM users WHERE email = :email AND is_deleted = FALSE",
            values={"email": email}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="Email not found.")
        
        if not hasattr(user, 'verification_code') or user.verification_code != code:
            raise HTTPException(status_code=401, detail="Invalid verification code.")
        
        # Check if code is expired
        code_time = user.code_generated_at
        if (datetime.utcnow() - code_time).total_seconds() > 600:
            raise HTTPException(status_code=401, detail="Verification code expired.")
        
        # Hash the new password
        password_hash = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Update password and clear verification code
        await database.execute(
            """
            UPDATE users 
            SET password_hash = :password_hash, 
                verification_code = NULL, 
                code_generated_at = NULL,
                updated_at = :now
            WHERE email = :email
            """,
            values={"password_hash": password_hash, "now": datetime.utcnow(), "email": email}
        )
        
        return {"success": True, "message": "Password updated successfully."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error updating password: {str(e)}")


# Change Password for Authenticated Users
class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@router.post("/api/change-password")
async def change_password(request: ChangePasswordRequest, user_id: UUID = Depends(get_current_user)):
    """Change password for authenticated user"""
    try:
        # Get current user's password hash
        user = await database.fetch_one(
            "SELECT password_hash FROM users WHERE id = :user_id AND is_deleted = FALSE",
            values={"user_id": user_id}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found.")
        
        # Verify current password
        if not bcrypt.checkpw(request.current_password.encode('utf-8'), user.password_hash.encode('utf-8')):
            raise HTTPException(status_code=401, detail="Current password is incorrect.")
        
        # Hash the new password
        new_password_hash = bcrypt.hashpw(request.new_password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        
        # Update password
        await database.execute(
            """
            UPDATE users 
            SET password_hash = :password_hash, 
                updated_at = :now
            WHERE id = :user_id
            """,
            values={"password_hash": new_password_hash, "now": datetime.utcnow(), "user_id": user_id}
        )
        
        return {"success": True, "message": "Password changed successfully."}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error changing password: {str(e)}")


# Login History Endpoints
@router.post("/api/login-history")
async def log_login_attempt(
    user_id: UUID = Depends(get_current_user),
    request: Request = None
):
    """Log a successful login attempt"""
    try:
        # Get client IP address
        client_ip = request.client.host if request and request.client else "unknown"
        
        # Get user agent from headers
        user_agent = request.headers.get("user-agent", "unknown") if request else "unknown"
        
        # Log the login attempt
        await database.execute(
            """
            INSERT INTO user_login_history (user_id, ip_address, user_agent, login_status, uuid_id)
            VALUES (:user_id, :ip_address, :user_agent, 'success', gen_random_uuid())
            """,
            values={
                "user_id": user_id,
                "ip_address": client_ip,
                "user_agent": user_agent
            }
        )
        
        return {"success": True, "message": "Login logged successfully"}
        
    except Exception as e:
        print(f"❌ Error logging login: {e}")
        # Don't fail the login if logging fails
        return {"success": False, "message": "Login logged but logging failed"}

@router.get("/api/login-history")
async def get_login_history(
    user_id: UUID = Depends(get_current_user),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100)
):
    """Get user's login history with pagination"""
    try:
        # Calculate offset for pagination
        offset = (page - 1) * limit
        
        # Get total count
        count_result = await database.fetch_one(
            """
            SELECT COUNT(*) as total
            FROM user_login_history 
            WHERE user_id = :user_id
            """,
            values={"user_id": user_id}
        )
        
        total_count = count_result.total if count_result else 0
        
        # Get paginated login history
        login_history = await database.fetch_all(
            """
            SELECT 
                id,
                login_timestamp,
                ip_address,
                user_agent,
                login_status,
                created_at
            FROM user_login_history 
            WHERE user_id = :user_id
            ORDER BY login_timestamp DESC
            LIMIT :limit OFFSET :offset
            """,
            values={
                "user_id": user_id,
                "limit": limit,
                "offset": offset
            }
        )
        
        # Convert to list of dicts and format timestamps
        history_list = []
        for record in login_history:
            history_list.append({
                "id": str(record["id"]),
                "login_timestamp": record["login_timestamp"].isoformat() if record["login_timestamp"] else None,
                "ip_address": str(record["ip_address"]) if record["ip_address"] else None,
                "user_agent": record["user_agent"],
                "login_status": record["login_status"],
                "created_at": record["created_at"].isoformat() if record["created_at"] else None
            })
        
        return {
            "success": True,
            "data": {
                "login_history": history_list,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total": total_count,
                    "pages": (total_count + limit - 1) // limit
                }
            }
        }
        
    except Exception as e:
        print(f"❌ Error getting login history: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving login history: {str(e)}")


@router.post("/api/login-history/cleanup")
async def cleanup_old_login_history(
    user_id: UUID = Depends(get_current_user)
):
    """Clean up old login history (older than 6 months) for the current user"""
    try:
        # Delete login history older than 6 months
        result = await database.execute(
            """
            DELETE FROM user_login_history 
            WHERE user_id = :user_id 
            AND login_timestamp < NOW() - INTERVAL '6 months'
            """,
            values={"user_id": user_id}
        )
        
        return {
            "success": True, 
            "message": "Old login history cleaned up successfully"
        }
        
    except Exception as e:
        print(f"❌ Error cleaning up login history: {e}")
        raise HTTPException(status_code=500, detail=f"Error cleaning up login history: {str(e)}")


# User Management Endpoints
class DeleteAccountRequest(BaseModel):
    reason: Optional[str] = "User requested account deletion"

@router.post("/api/delete-account")
async def delete_account(
    request: DeleteAccountRequest,
    user_id: UUID = Depends(get_current_user)
):
    """Soft delete user account"""
    try:
        success, message = await UserManagementService.soft_delete_user(
            str(user_id), 
            request.reason
        )
        
        if success:
            return {
                "success": True,
                "message": message,
                "deletion_date": datetime.utcnow().isoformat()
            }
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error deleting account: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting account: {str(e)}")


@router.post("/api/reactivate-account")
async def reactivate_account(
    email: EmailStr,
    user_id: str
):
    """Reactivate a soft-deleted user account"""
    try:
        success, message = await UserManagementService.reactivate_user(user_id)
        
        if success:
            return {
                "success": True,
                "message": message
            }
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error reactivating account: {e}")
        raise HTTPException(status_code=500, detail=f"Error reactivating account: {str(e)}")


@router.post("/api/cleanup-expired-deletions")
async def cleanup_expired_deletions(admin_id: UUID = Depends(get_super_admin_user)):
    """Clean up expired user deletions (super admin only)"""
    try:
        success, message, count = await UserManagementService.cleanup_expired_deletions()
        
        if success:
            return {
                "success": True,
                "message": message,
                "deleted_count": count
            }
        else:
            raise HTTPException(status_code=400, detail=message)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error cleaning up expired deletions: {e}")
        raise HTTPException(status_code=500, detail=f"Error cleaning up expired deletions: {str(e)}")

# Google OAuth Routes
from services.google_oauth_service import GoogleOAuthService
import secrets

@router.get("/api/auth/google/url")
async def get_google_auth_url():
    """Get Google OAuth authorization URL"""
    try:
        google_service = GoogleOAuthService()
        state = secrets.token_urlsafe(32)  # Generate random state for security
        
        auth_url = google_service.get_authorization_url(state)
        
        return JSONResponse(
            content={
                "success": True,
                "auth_url": auth_url,
                "state": state
            },
            headers={
                "Access-Control-Allow-Origin": "*",  # Temporary fix for CORS
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Requested-With, Accept, Origin"
            }
        )
    except Exception as e:
        print(f"❌ Error generating Google auth URL: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating Google auth URL: {str(e)}")

@router.get("/api/auth/google/callback")
async def google_oauth_callback(
    code: str,
    state: str = None,
    timezone: str = "America/Los_Angeles|Los Angeles, USA",
    request: Request = None
):
    """Handle Google OAuth callback for both login and Google Sheets"""
    try:
        # Check if this is a Google Sheets OAuth request
        is_google_sheets_request = state and ':' in state
        
        if is_google_sheets_request:
            # Handle Google Sheets OAuth
            return await handle_google_sheets_callback(code, state, request)
        else:
            # Handle regular Google login OAuth
            return await handle_google_login_callback(code, state, timezone, request)
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in Google OAuth callback: {e}")
        raise HTTPException(status_code=500, detail=f"Error in Google OAuth callback: {str(e)}")

async def handle_google_login_callback(code: str, state: str = None, timezone: str = "America/Los_Angeles|Los Angeles, USA", request: Request = None):
    """Handle regular Google login OAuth callback"""
    try:
        google_service = GoogleOAuthService()
        
        # Exchange code for tokens
        tokens = await google_service.exchange_code_for_tokens(code)
        access_token = tokens.get("access_token")
        
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token received from Google")
        
        # Verify access token and get user info
        user_info = await google_service.verify_id_token(access_token)
        
        # Log the timezone being used
        print(f"🕐 Google OAuth login with timezone: {timezone}")
        
        # Check if user exists (including deleted users for reactivation)
        existing_user = await database.fetch_one(
            "SELECT * FROM users WHERE email = :email",
            values={"email": user_info["email"].lower()}
        )
        
        if existing_user:
            # Check if user is deleted and needs reactivation
            if hasattr(existing_user, 'is_deleted') and existing_user.is_deleted:
                return {
                    "success": False,
                    "needs_reactivation": True,
                    "message": "Account was previously deleted",
                    "user_email": existing_user.email,
                    "user_id": str(existing_user.id),
                    "google_id": user_info["google_id"]
                }
            else:
                # User exists and is active - update Google ID if not set
                if not hasattr(existing_user, 'google_id') or not existing_user.google_id:
                    await database.execute(
                        "UPDATE users SET google_id = :google_id WHERE id = :user_id",
                        values={"google_id": user_info["google_id"], "user_id": str(existing_user.id)}
                    )
                
                # Update last_login_at for existing user
                await database.execute(
                    "UPDATE users SET last_login_at = :now WHERE id = :user_id",
                    values={"now": datetime.utcnow(), "user_id": str(existing_user.id)}
                )
                
                # Record login history for existing user
                try:
                    # Get client IP address from request
                    client_ip = request.client.host if request and hasattr(request, 'client') and request.client else "unknown"
                    
                    # Get user agent from headers
                    user_agent = request.headers.get("user-agent", "unknown") if request else "unknown"
                    
                    # Insert into login history
                    await database.execute(
                        """
                        INSERT INTO user_login_history (user_id, ip_address, user_agent, login_status, uuid_id)
                        VALUES (:user_id, :ip_address, :user_agent, :login_status, gen_random_uuid())
                        """,
                        values={
                            "user_id": str(existing_user.id),
                            "ip_address": client_ip,
                            "user_agent": user_agent,
                            "login_status": "success"
                        }
                    )
                    print(f"✅ Login history recorded for existing user: {existing_user.email}")
                except Exception as e:
                    print(f"❌ Failed to record login history for existing user: {e}")
                    # Don't fail login if history recording fails
            
            # Generate JWT token for existing user
            token = create_session_token(existing_user.email, str(existing_user.id))

            return _session_auth_response(
                {
                    "success": True,
                    "message": "Login successful",
                    "user_exists": True,
                    "email": existing_user.email,
                },
                token,
            )
        else:
            # Create new user with race condition protection
            try:
                user_id = str(uuid4())
                now = datetime.utcnow()
                
                await database.execute(
                    """
                    INSERT INTO users (
                        id, email, first_name, last_name, google_id, 
                        timezone, is_active, created_at, updated_at
                    ) VALUES (
                        :user_id, :email, :first_name, :last_name, :google_id,
                        :timezone, TRUE, :now, :now
                    )
                    """,
                    values={
                        "user_id": user_id,
                        "email": user_info["email"].lower(),
                        "first_name": user_info["first_name"],
                        "last_name": user_info["last_name"],
                        "google_id": user_info["google_id"],
                        "timezone": timezone,
                        "now": now
                    }
                )
                
                # Create default free plan for new user
                await database.execute(
                    """
                    INSERT INTO user_plans (
                        user_id, plan_type, status, current_period_start, 
                        current_period_end, created_at, updated_at
                    ) VALUES (
                        :user_id, 'free', 'active', :now, :now, :now, :now
                    )
                    """,
                    values={"user_id": user_id, "now": now}
                )
                # One-time free signup credits (500); no monthly refill
                try:
                    from services.entitlements import get_entitlements
                    await get_entitlements().grant_free_signup(user_id)
                    print(f"✅ Free signup credits granted for user {user_id}")
                except Exception as cred_e:
                    print(f"⚠️ Failed to grant free signup credits: {cred_e}")

                # Send welcome email to new user
                try:
                    await send_welcome_email(user_info["email"].lower(), user_info["first_name"])
                    print(f"✅ Welcome email sent to {user_info['email'].lower()}")
                except Exception as e:
                    print(f"❌ Failed to send welcome email to {user_info['email'].lower()}: {e}")
                    # Don't fail signup if email fails
                
                # Update last_login_at for new user (set to creation time)
                await database.execute(
                    "UPDATE users SET last_login_at = :now WHERE id = :user_id",
                    values={"now": now, "user_id": user_id}
                )
                
                # Record login history for new user
                try:
                    # Get client IP address from request
                    client_ip = request.client.host if request and hasattr(request, 'client') and request.client else "unknown"
                    
                    # Get user agent from headers
                    user_agent = request.headers.get("user-agent", "unknown") if request else "unknown"
                    
                    # Insert into login history
                    await database.execute(
                        """
                        INSERT INTO user_login_history (user_id, ip_address, user_agent, login_status, uuid_id)
                        VALUES (:user_id, :ip_address, :user_agent, :login_status, gen_random_uuid())
                        """,
                        values={
                            "user_id": user_id,
                            "ip_address": client_ip,
                            "user_agent": user_agent,
                            "login_status": "success"
                        }
                    )
                    print(f"✅ Login history recorded for new user: {user_info['email'].lower()}")
                except Exception as e:
                    print(f"❌ Failed to record login history for new user: {e}")
                    # Don't fail signup if history recording fails
                
                # Generate JWT token for new user
                token = create_session_token(user_info["email"].lower(), user_id)

                return _session_auth_response(
                    {
                        "success": True,
                        "message": "Account created successfully",
                        "user_exists": False,
                        "email": user_info["email"].lower(),
                    },
                    token,
                )
                
            except Exception as e:
                # Handle race condition - user might have been created by another request
                if "duplicate key value violates unique constraint" in str(e):
                    
                    # Try to fetch the user that was just created
                    existing_user = await database.fetch_one(
                        "SELECT * FROM users WHERE email = :email",
                        values={"email": user_info["email"].lower()}
                    )
                    
                    if existing_user:
                        
                        # Update Google ID for the existing user
                        await database.execute(
                            "UPDATE users SET google_id = :google_id WHERE id = :user_id",
                            values={"google_id": user_info["google_id"], "user_id": str(existing_user.id)}
                        )
                        
                        # Send welcome email to new user (in case they're new via race condition)
                        try:
                            await send_welcome_email(existing_user.email, user_info["first_name"])
                            print(f"✅ Welcome email sent to {existing_user.email}")
                        except Exception as e:
                            print(f"❌ Failed to send welcome email to {existing_user.email}: {e}")
                            # Don't fail signup if email fails
                        
                        # Update last_login_at for race condition user
                        await database.execute(
                            "UPDATE users SET last_login_at = :now WHERE id = :user_id",
                            values={"now": datetime.utcnow(), "user_id": str(existing_user.id)}
                        )
                        
                        # Record login history for race condition user
                        try:
                            # Get client IP address from request
                            client_ip = request.client.host if request and hasattr(request, 'client') and request.client else "unknown"
                            
                            # Get user agent from headers
                            user_agent = request.headers.get("user-agent", "unknown") if request else "unknown"
                            
                            # Insert into login history
                            await database.execute(
                                """
                                INSERT INTO user_login_history (user_id, ip_address, user_agent, login_status, uuid_id)
                                VALUES (:user_id, :ip_address, :user_agent, :login_status, gen_random_uuid())
                                """,
                                values={
                                    "user_id": str(existing_user.id),
                                    "ip_address": client_ip,
                                    "user_agent": user_agent,
                                    "login_status": "success"
                                }
                            )
                            print(f"✅ Login history recorded for race condition user: {existing_user.email}")
                        except Exception as e:
                            print(f"❌ Failed to record login history for race condition user: {e}")
                            # Don't fail login if history recording fails
                        
                        # Generate JWT token for existing user
                        token = create_session_token(existing_user.email, str(existing_user.id))

                        return _session_auth_response(
                            {
                                "success": True,
                                "message": "Account linked successfully",
                                "user_exists": True,
                                "email": existing_user.email,
                            },
                            token,
                        )
                    else:
                        # This shouldn't happen, but handle it gracefully
                        raise HTTPException(status_code=500, detail="Failed to create or find user account")
                else:
                    # Re-raise other errors
                    raise
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in Google OAuth callback: {e}")
        print(f"❌ Error type: {type(e)}")
        import traceback
        print(f"❌ Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error in Google OAuth callback: {str(e)}")

async def handle_google_sheets_callback(code: str, state: str, request: Request = None):
    """Handle Google Sheets OAuth callback"""
    try:
        google_sheets_service = GoogleSheetsOAuthService()
        
        # Exchange code for tokens
        tokens = await google_sheets_service.exchange_code_for_tokens(code)
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in", 3600)
        
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token received from Google")
        
        # Verify access token and get user info
        user_info = await google_sheets_service.verify_and_get_user_info(access_token)
        
        # Calculate expiration time
        expires_at = google_sheets_service.calculate_expires_at(expires_in)
        
        # Extract user_id (and optional reconnect connection_id) from state
        try:
            from services.google_sheets_connection_service import (
                parse_google_sheets_oauth_state,
                reconnect_from_oauth,
                upsert_from_oauth,
            )

            user_id, reconnect_connection_id = parse_google_sheets_oauth_state(state)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        account_name = f"{user_info.get('first_name', '')} {user_info.get('last_name', '')}".strip()
        reconnected = bool(reconnect_connection_id)
        try:
            if reconnect_connection_id:
                connection_id = await reconnect_from_oauth(
                    user_id=user_id,
                    connection_id=reconnect_connection_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    email=user_info.get("email"),
                    account_name=account_name,
                )
            else:
                connection_id = await upsert_from_oauth(
                    user_id=user_id,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    email=user_info.get("email"),
                    account_name=account_name,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        
        redirect_url = f"/connections?gsConnection={connection_id}"
        if reconnected:
            redirect_url += "&reconnected=1"
        
        # Return success response for Google Sheets OAuth
        return JSONResponse(
            content={
                "success": True,
                "message": "Google Sheets connected successfully",
                "connection_id": connection_id,
                "reconnected": reconnected,
                "redirect_url": redirect_url,
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Requested-With, Accept, Origin"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error in Google Sheets OAuth callback: {e}")
        raise HTTPException(status_code=500, detail=f"Error in Google Sheets OAuth callback: {str(e)}")

@router.post("/api/auth/google/reactivate")
async def reactivate_google_account(
    user_id: str = Body(...),
    google_id: str = Body(...)
):
    """Reactivate a deleted user account via Google OAuth"""
    try:
        # Verify the user exists and is deleted
        user = await database.fetch_one(
            "SELECT * FROM users WHERE id = :user_id AND is_deleted = TRUE",
            values={"user_id": user_id}
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="Deleted user not found")
        
        # ✅ Use the proper service method to reactivate (handles plans, subscriptions, related data)
        success, message = await UserManagementService.reactivate_user(user_id)
        
        if not success:
            raise HTTPException(status_code=400, detail=message)
        
        # Update Google ID after reactivation
        await database.execute(
            "UPDATE users SET google_id = :google_id, updated_at = NOW() WHERE id = :user_id",
            values={"google_id": google_id, "user_id": user_id}
        )
        
        # Fetch user to get email for JWT token
        reactivated_user = await database.fetch_one(
            "SELECT id, email FROM users WHERE id = :user_id",
            values={"user_id": user_id}
        )
        
        # Generate JWT token for reactivated user
        token = create_session_token(reactivated_user.email, user_id)

        return _session_auth_response(
            {"success": True, "message": "Account reactivated successfully", "email": reactivated_user.email},
            token,
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error reactivating Google OAuth account: {e}")
        raise HTTPException(status_code=500, detail=f"Error reactivating account: {str(e)}")

# Google Sheets OAuth Routes
from services.google_sheets_oauth_service import GoogleSheetsOAuthService

@router.get("/oauth/google-sheets/start")
async def start_google_sheets_oauth(
    connection_id: Optional[UUID] = None,
    user_id: UUID = Depends(get_current_user),
):
    """Start Google Sheets OAuth flow (new connect or reconnect existing connection)."""
    try:
        from services.google_sheets_connection_service import get_connection

        google_sheets_service = GoogleSheetsOAuthService()
        login_hint = None
        if connection_id:
            conn = await get_connection(str(user_id), str(connection_id))
            if not conn:
                raise HTTPException(status_code=404, detail="Google Sheets connection not found")
            login_hint = conn.get("google_account_email")
            state = f"{secrets.token_urlsafe(32)}:{str(user_id)}:{str(connection_id)}"
        else:
            state = f"{secrets.token_urlsafe(32)}:{str(user_id)}"

        auth_url = google_sheets_service.get_authorization_url(state, login_hint=login_hint)
        
        return JSONResponse(
            content={
                "success": True,
                "authorization_url": auth_url,
                "state": state,
                "reconnect": bool(connection_id),
            },
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Requested-With, Accept, Origin"
            }
        )
    except Exception as e:
        print(f"❌ Error generating Google Sheets auth URL: {e}")
        raise HTTPException(status_code=500, detail=f"Error generating Google Sheets auth URL: {str(e)}")


@router.get("/api/google-sheets-connections")
async def list_google_sheets_connections(user_id: UUID = Depends(get_current_user)):
    """List all active Google Sheets connections for the current user."""
    from services.google_sheets_connection_service import list_connections

    try:
        connections = await list_connections(str(user_id))
        return {"success": True, "connections": connections, "count": len(connections)}
    except Exception as e:
        print(f"❌ Error listing Google Sheets connections: {e}")
        raise HTTPException(status_code=500, detail=f"Error listing Google Sheets connections: {str(e)}")


@router.get("/api/google-sheets-connections/{connection_id}")
async def get_google_sheets_connection_by_id(
    connection_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Get one Google Sheets connection by id."""
    from services.google_sheets_connection_service import get_connection

    conn = await get_connection(str(user_id), str(connection_id), refresh_token=True)
    if not conn:
        raise HTTPException(status_code=404, detail="Google Sheets connection not found")
    return {"success": True, "connection": conn}


class GoogleSheetsConnectionPatch(BaseModel):
    connection_name: Optional[str] = None


@router.patch("/api/google-sheets-connections/{connection_id}")
async def patch_google_sheets_connection(
    connection_id: UUID,
    body: GoogleSheetsConnectionPatch,
    user_id: UUID = Depends(get_current_user),
):
    """Update connection display name."""
    from services.google_sheets_connection_service import update_connection_name

    if body.connection_name is None:
        raise HTTPException(status_code=400, detail="connection_name is required")

    conn = await update_connection_name(
        str(user_id),
        str(connection_id),
        body.connection_name,
    )
    if not conn:
        raise HTTPException(status_code=404, detail="Google Sheets connection not found")
    return {"success": True, "connection": conn}


@router.delete("/api/google-sheets-connections/{connection_id}")
async def delete_google_sheets_connection_by_id(
    connection_id: UUID,
    user_id: UUID = Depends(get_current_user),
):
    """Delete one Google Sheets connection and its workbooks/schemas."""
    from services.google_sheets_connection_service import delete_connection

    deleted = await delete_connection(str(user_id), str(connection_id))
    if not deleted:
        raise HTTPException(status_code=404, detail="Google Sheets connection not found")
    return {
        "success": True,
        "message": "Google Sheets connection and its workbooks deleted successfully",
    }


@router.get("/api/google-sheets-connection")
async def get_google_sheets_connection(user_id: UUID = Depends(get_current_user)):
    """Get most recently connected Google Sheets account (legacy single-connection API)."""
    from db.extracted_schema import GoogleSheetsConnectionResponse, GoogleSheetsConnection
    from services.google_sheets_connection_service import get_newest_connection

    try:
        conn = await get_newest_connection(str(user_id), refresh_token=True)
        if conn:
            return GoogleSheetsConnectionResponse(
                connected=True,
                connection=GoogleSheetsConnection(
                    id=conn["id"],
                    user_id=conn["user_id"],
                    connection_name=conn.get("connection_name") or "Google Sheets",
                    google_account_email=conn.get("google_account_email"),
                    google_account_name=conn.get("google_account_name"),
                    access_token=conn.get("access_token"),
                    is_active=conn.get("is_active", True),
                    created_at=conn["created_at"],
                    updated_at=conn["updated_at"],
                ),
            )
        return GoogleSheetsConnectionResponse(
            connected=False,
            message="No Google Sheets connection found",
        )
            
    except Exception as e:
        print(f"❌ Error fetching Google Sheets connection: {e}")
        raise HTTPException(status_code=500, detail=f"Error fetching Google Sheets connection: {str(e)}")

@router.delete("/api/google-sheets-connection")
async def disconnect_google_sheets(user_id: UUID = Depends(get_current_user)):
    """Disconnect the most recent Google Sheets connection (legacy API)."""
    from services.google_sheets_connection_service import delete_connection, get_newest_connection

    try:
        conn = await get_newest_connection(str(user_id), refresh_token=False)
        if not conn:
            raise HTTPException(status_code=404, detail="No active Google Sheets connection found")

        deleted = await delete_connection(str(user_id), conn["id"])
        if not deleted:
            raise HTTPException(status_code=404, detail="No active Google Sheets connection found")

        return {
            "success": True,
            "message": "Google Sheets connection and its workbooks deleted successfully",
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error disconnecting Google Sheets: {e}")
        raise HTTPException(status_code=500, detail=f"Error disconnecting Google Sheets: {str(e)}")