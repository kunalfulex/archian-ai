"""Salesforce OAuth start/callback and Edison SSO helpers."""

import asyncio
import base64
import hashlib
import os
import secrets
import traceback
import urllib.parse
from datetime import datetime
from typing import Dict, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from db.connection import database
from routes.auth import get_current_user
from utils.cors_origins import normalize_origin
from utils.salesforceapimigration import fetch_and_store_salesforce_objects
from utils.url_defaults import DEFAULT_FRONTEND_URL

router = APIRouter()


REDIRECT_URI = os.getenv("SALESFORCE_REDIRECT_URI")


# Archian SSO: Shared Connected App credentials for "Connect via SSO" (Fivetran-style)
# When set, users can connect Salesforce with one click—no manual Connected App setup
SALESFORCE_EDISON_CLIENT_ID = os.getenv("SALESFORCE_EDISON_CLIENT_ID")


SALESFORCE_EDISON_CLIENT_SECRET = os.getenv("SALESFORCE_EDISON_CLIENT_SECRET")


SALESFORCE_EDISON_SANDBOX_CLIENT_ID = os.getenv("SALESFORCE_EDISON_SANDBOX_CLIENT_ID")


SALESFORCE_EDISON_SANDBOX_CLIENT_SECRET = os.getenv("SALESFORCE_EDISON_SANDBOX_CLIENT_SECRET")


# Memory store for code_verifier (replace with persistent store if needed)
code_verifiers = {}


# Configure CORS - Secure configuration
FRONTEND_URL = normalize_origin(os.getenv("FRONTEND_URL", DEFAULT_FRONTEND_URL))


@router.get("/oauth/start")
async def start_salesforce_oauth(
    connected_app_id: Optional[int] = Query(None),
    user_id: UUID = Depends(get_current_user),
):

    # Get user's Connected App config
    if connected_app_id is None:
        raise HTTPException(status_code=400, detail="connected_app_id is required")

    connected_app = await database.fetch_one(
        """
        SELECT ca.id, ca.client_id
        FROM salesforce_connected_apps ca
        WHERE ca.user_id = :user_id
          AND ca.id = :connected_app_id
        LIMIT 1
        """,
        values={"user_id": user_id, "connected_app_id": connected_app_id},
    )

    if not connected_app:
        raise HTTPException(status_code=404, detail="Connected App not found for this user.")

    # environment_type is on salesforce_tokens; get from token for this app or default production
    token_row = await database.fetch_one(
        """
        SELECT environment_type FROM salesforce_tokens
        WHERE user_id = :user_id AND connected_app_id = :connected_app_id
        ORDER BY issued_at DESC LIMIT 1
        """,
        values={"user_id": user_id, "connected_app_id": connected_app_id},
    )
    token_row_dict = dict(token_row) if token_row else {}
    environment = token_row_dict.get("environment_type") or "production"

    # Always use login/test for authorize URL so we support connecting new orgs
    # and reconnecting. Using an existing org's instance_url would send users
    # to the wrong org when connecting a different org.
    if environment == "sandbox":
        auth_url_base = "https://test.salesforce.com/services/oauth2/authorize"
    else:
        auth_url_base = "https://login.salesforce.com/services/oauth2/authorize"

    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")

    # Save state + code_verifier with user email (temporary memory store)
    code_verifiers[state] = {
        "user_id": user_id,
        "code_verifier": code_verifier,
        "connected_app_id": connected_app["id"],
        "created_at": datetime.utcnow()
    }

    params = {
        "response_type": "code",
        "client_id": connected_app["client_id"],
        "redirect_uri": REDIRECT_URI,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256"
    }

    auth_url = f"{auth_url_base}?{urllib.parse.urlencode(params)}"
    
    return JSONResponse(content={"authorization_url": auth_url})


@router.get("/api/salesforce-sso-available")
async def salesforce_sso_available(user_id: UUID = Depends(get_current_user)):
    """
    Check if Connect via SSO is available (Archian's shared Connected App is configured).
    When True, frontend shows "Connect via SSO" button—users connect with one click.
    """
    available = bool(SALESFORCE_EDISON_CLIENT_ID and SALESFORCE_EDISON_CLIENT_SECRET)
    return {"available": available}


def _get_edison_sso_credentials(environment: str) -> dict:
    """Return Archian SSO client_id/secret and display info for the given environment."""
    if environment == "sandbox":
        client_id = SALESFORCE_EDISON_SANDBOX_CLIENT_ID or SALESFORCE_EDISON_CLIENT_ID
        client_secret = SALESFORCE_EDISON_SANDBOX_CLIENT_SECRET or SALESFORCE_EDISON_CLIENT_SECRET
        connection_name = "Sandbox (Archian SSO)"
    else:
        client_id = SALESFORCE_EDISON_CLIENT_ID
        client_secret = SALESFORCE_EDISON_CLIENT_SECRET
        connection_name = "Production (Archian SSO)"

    if not client_id or not client_secret:
        raise ValueError("Archian SSO not configured for this environment")
    return {"client_id": client_id, "client_secret": client_secret, "connection_name": connection_name, "environment": environment}


def _salesforce_global_oauth_urls(environment: str) -> Dict[str, str]:
    """OAuth endpoints on login.salesforce.com / test.salesforce.com (new org connect)."""
    base = "https://test.salesforce.com" if environment == "sandbox" else "https://login.salesforce.com"
    return {
        "authorize": f"{base}/services/oauth2/authorize",
        "token": f"{base}/services/oauth2/token",
        "userinfo": f"{base}/services/oauth2/userinfo",
    }


def _salesforce_instance_oauth_urls(instance_url: str) -> Dict[str, str]:
    """OAuth endpoints on the org My Domain (reconnect specific org)."""
    base = (instance_url or "").strip().rstrip("/")
    if not base.lower().startswith("https://"):
        raise ValueError("Invalid Salesforce instance URL")
    return {
        "authorize": f"{base}/services/oauth2/authorize",
        "token": f"{base}/services/oauth2/token",
        "userinfo": f"{base}/services/oauth2/userinfo",
    }


@router.get("/oauth/start-sso")
async def start_salesforce_oauth_sso(
    environment: str = Query("production", regex="^(production|sandbox)$"),
    org_id: Optional[str] = Query(None),
    user_id: UUID = Depends(get_current_user),
):
    """
    Start Salesforce OAuth using Archian's shared Connected App (Connect via SSO).
    No Consumer Key/Secret required—users just log in to Salesforce.
    """
    if not SALESFORCE_EDISON_CLIENT_ID or not SALESFORCE_EDISON_CLIENT_SECRET:
        raise HTTPException(
            status_code=503,
            detail="Connect via SSO is not configured. Please use the Connected App setup or contact support.",
        )

    creds = _get_edison_sso_credentials(environment)
    client_id = creds["client_id"]
    client_secret = creds["client_secret"]
    connection_name = creds["connection_name"]
    env_type = creds["environment"]

    # Get or create salesforce_connected_apps row for this user
    existing = await database.fetch_one(
        """
        SELECT id FROM salesforce_connected_apps
        WHERE user_id = :user_id AND client_id = :client_id
        LIMIT 1
        """,
        values={"user_id": user_id, "client_id": client_id},
    )

    if existing:
        connected_app_id = existing["id"]
    else:
        # Token-centric: only store credentials. is_default lives on salesforce_tokens.
        redirect_uri = REDIRECT_URI or f"{FRONTEND_URL}/oauth/callback"
        insert_row = await database.fetch_one(
            """
            INSERT INTO salesforce_connected_apps (user_id, client_id, client_secret, redirect_uri, created_at, updated_at, uuid_id)
            VALUES (:user_id, :client_id, :client_secret, :redirect_uri, :created_at, :updated_at, gen_random_uuid())
            RETURNING id
            """,
            values={
                "user_id": user_id,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            },
        )
        connected_app_id = insert_row["id"] if insert_row else None

    oauth_urls = _salesforce_global_oauth_urls(env_type)
    expected_org_id = None
    login_hint = None
    is_reconnect = False

    if org_id:
        token_row = await database.fetch_one(
            """
            SELECT org_id, instance_url, salesforce_user_email, environment_type
            FROM salesforce_tokens
            WHERE user_id = :user_id AND org_id = :org_id
            ORDER BY issued_at DESC
            LIMIT 1
            """,
            values={"user_id": user_id, "org_id": org_id},
        )
        if not token_row:
            raise HTTPException(status_code=404, detail="Salesforce org not found for reconnect.")
        token_meta = dict(token_row)
        instance_url = token_meta.get("instance_url")
        if not instance_url:
            raise HTTPException(status_code=400, detail="Org is missing instance URL; reconnect from Salesforce login.")
        try:
            oauth_urls = _salesforce_instance_oauth_urls(instance_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        expected_org_id = str(token_meta["org_id"])
        login_hint = token_meta.get("salesforce_user_email")
        is_reconnect = True
        env_from_token = token_meta.get("environment_type")
        if env_from_token in ("production", "sandbox"):
            env_type = env_from_token

    state = secrets.token_urlsafe(16)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).decode().rstrip("=")

    redirect_uri = REDIRECT_URI or f"{FRONTEND_URL}/oauth/callback"
    code_verifiers[state] = {
        "user_id": user_id,
        "code_verifier": code_verifier,
        "connected_app_id": connected_app_id,
        "is_sso": True,
        "is_reconnect": is_reconnect,
        "expected_org_id": expected_org_id,
        "token_url": oauth_urls["token"],
        "userinfo_url": oauth_urls["userinfo"],
        "environment_type": env_type,
        "created_at": datetime.utcnow(),
    }

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint

    auth_url = f"{oauth_urls['authorize']}?{urllib.parse.urlencode(params)}"
    return JSONResponse(content={"authorization_url": auth_url})


@router.post("/oauth/callback")
async def salesforce_oauth_callback(request: Request):
    body = await request.json()
    code = body.get("code")
    state = body.get("state")

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    code_verifier_data = code_verifiers.get(state)
    if not code_verifier_data:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    user_id = code_verifier_data["user_id"]
    code_verifier = code_verifier_data["code_verifier"]
    connected_app_id = code_verifier_data["connected_app_id"]
    is_sso = code_verifier_data.get("is_sso", False)
    expected_org_id = code_verifier_data.get("expected_org_id")

    # Remove state entry after use to prevent reuse
    code_verifiers.pop(state, None)

    # Fetch Connected App
    connected_app = await database.fetch_one(
        """
        SELECT ca.id, ca.client_id, ca.client_secret
        FROM salesforce_connected_apps ca
        WHERE ca.user_id = :user_id
          AND ca.id = :connected_app_id
        LIMIT 1
        """,
        values={"user_id": user_id, "connected_app_id": connected_app_id},
    )

    if not connected_app:
        print(f"❌ Connected App not found for user_id: {user_id}, connected_app_id: {connected_app_id}")
        raise HTTPException(status_code=404, detail="Connected App not found for this user.")

    token_url = code_verifier_data.get("token_url")
    userinfo_url = code_verifier_data.get("userinfo_url")
    if not token_url or not userinfo_url:
        environment = code_verifier_data.get("environment_type")
        if not environment:
            token_row = await database.fetch_one(
                """
                SELECT environment_type FROM salesforce_tokens
                WHERE user_id = :user_id AND connected_app_id = :connected_app_id
                ORDER BY issued_at DESC LIMIT 1
                """,
                values={"user_id": user_id, "connected_app_id": connected_app_id},
            )
            token_row_dict = dict(token_row) if token_row else {}
            environment = token_row_dict.get("environment_type") or "production"
        oauth_urls = _salesforce_global_oauth_urls(environment)
        token_url = oauth_urls["token"]
        userinfo_url = oauth_urls["userinfo"]

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": connected_app["client_id"],
        "client_secret": connected_app["client_secret"],
        "redirect_uri": REDIRECT_URI,
        "code_verifier": code_verifier
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token_response = await client.post(token_url, data=payload)
            token_response.raise_for_status()
            token_data = token_response.json()
            
            userinfo_response = await client.get(
                userinfo_url,
                headers={"Authorization": f"Bearer {token_data['access_token']}"}
            )
            userinfo_response.raise_for_status()
            userinfo = userinfo_response.json()
        sf_username = userinfo.get("preferred_username") or userinfo.get("email")
        org_id = userinfo.get("organization_id")

        if expected_org_id and org_id and str(org_id) != str(expected_org_id):
            raise HTTPException(
                status_code=400,
                detail=(
                    "You signed into a different Salesforce org than the one you were reconnecting. "
                    "Please try again and use the account for this org."
                ),
            )

        # For SSO: fetch existing token for this org to get old connected_app_id (manual app to delete if orphaned)
        old_connected_app_id = None
        if is_sso:
            existing_token = await database.fetch_one(
                "SELECT connected_app_id FROM salesforce_tokens WHERE user_id = :user_id AND org_id = :org_id",
                values={"user_id": user_id, "org_id": org_id},
            )
            if existing_token and existing_token["connected_app_id"] != connected_app["id"]:
                old_connected_app_id = existing_token["connected_app_id"]

        # Check if this org already existed (reconnect vs first connect)
        previous_token = await database.fetch_one(
            "SELECT org_id FROM salesforce_tokens WHERE user_id = :user_id AND org_id = :org_id",
            values={"user_id": user_id, "org_id": org_id},
        )

        # Parse issued_at timestamp
        issued_at_str = token_data.get("issued_at")
        issued_at_dt = datetime.utcfromtimestamp(int(issued_at_str) / 1000)

        is_sso_connection = is_sso

        # Insert new token with the existing constraint (user_id, org_id)
        try:
            await database.execute(
                """
                INSERT INTO salesforce_tokens (user_id, edison_user_email, salesforce_user_email, access_token, refresh_token, instance_url, issued_at, token_type, org_id, connected_app_id, is_sso_connection)
                VALUES (:user_id, :edison_user_email, :salesforce_user_email, :access_token, :refresh_token, :instance_url, :issued_at, :token_type, :org_id, :connected_app_id, :is_sso_connection)
                ON CONFLICT (user_id, org_id) DO UPDATE
                SET edison_user_email = EXCLUDED.edison_user_email,
                    salesforce_user_email = EXCLUDED.salesforce_user_email,
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    instance_url = EXCLUDED.instance_url,
                    issued_at = EXCLUDED.issued_at,
                    token_type = EXCLUDED.token_type,
                    org_id = EXCLUDED.org_id,
                    connected_app_id = EXCLUDED.connected_app_id,
                    is_sso_connection = EXCLUDED.is_sso_connection,
                    categorization_completed = FALSE
                """,
                values={
                    "user_id": user_id,
                    "edison_user_email": "",
                    "salesforce_user_email": sf_username,
                    "access_token": token_data["access_token"],
                    "refresh_token": token_data.get("refresh_token"),
                    "instance_url": token_data["instance_url"],
                    "issued_at": issued_at_dt,
                    "token_type": token_data["token_type"],
                    "org_id": org_id,
                    "connected_app_id": connected_app["id"],
                    "is_sso_connection": is_sso_connection,
                }
            )
        except Exception as e:
            print(f"❌ Error inserting Salesforce token: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"Failed to insert Salesforce token: {str(e)}")

        # SSO override: if user reconnected this org via SSO, delete the orphaned manual Connected App
        if is_sso and old_connected_app_id:
            token_count = await database.fetch_val(
                "SELECT COUNT(*) FROM salesforce_tokens WHERE connected_app_id = :connected_app_id",
                values={"connected_app_id": old_connected_app_id},
            )
            if token_count == 0:
                await database.execute(
                    "DELETE FROM salesforce_connected_apps WHERE id = :id",
                    values={"id": old_connected_app_id},
                )
                print(f"🗑️ Removed orphaned manual Connected App (id={old_connected_app_id}) after SSO override for org {org_id}")

        # Start Salesforce schema sync in background (non-blocking)
        try:
            import asyncio
            asyncio.create_task(
                fetch_and_store_salesforce_objects(user_id, token_data["instance_url"], token_data["access_token"], org_id)
            )
            print(f"🚀 Started background Salesforce schema sync for org {org_id}")
        except Exception as e:
            print(f"❌ Error starting Salesforce schema sync: {e}")
            import traceback
            traceback.print_exc()
            # Don't fail the entire OAuth process if schema fetching fails
        
        connector_id_row = await database.fetch_one(
            """
            SELECT id::text AS connector_id FROM salesforce_tokens
            WHERE user_id = :user_id AND org_id = :org_id
            ORDER BY issued_at DESC LIMIT 1
            """,
            values={"user_id": user_id, "org_id": org_id},
        )
        connector_id = dict(connector_id_row).get("connector_id") if connector_id_row else None
        is_reconnect = previous_token is not None

        org_display_row = await database.fetch_one(
            """
            SELECT NULLIF(TRIM(org_display_name), '') AS org_display_name
            FROM salesforce_tokens
            WHERE user_id = :user_id AND org_id = :org_id
            ORDER BY issued_at DESC LIMIT 1
            """,
            values={"user_id": user_id, "org_id": org_id},
        )
        org_display_name = (
            dict(org_display_row).get("org_display_name") if org_display_row else None
        )

        return JSONResponse(
            content={
                "status": "success",
                "salesforce_username": sf_username,
                "org_id": org_id,
                "connector_id": connector_id,
                "org_display_name": org_display_name,
                "is_reconnect": is_reconnect,
            }
        )

    except httpx.HTTPStatusError as e:
        response = e.response
        print(f"❌ HTTP Status Error in OAuth callback: {str(e)}")
        if response is not None:
            print(f"   Response Status: {response.status_code}")
            print(f"   Response Text: {response.text}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"OAuth token exchange failed: {response.text}")
        else:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"OAuth token exchange failed: {str(e)}")
    except httpx.RequestError as e:
        print(f"❌ Request Error in OAuth callback: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"OAuth token exchange failed: {str(e)}")
    except Exception as e:
        print(f"❌ Unexpected error in OAuth callback: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"OAuth callback failed: {str(e)}")
