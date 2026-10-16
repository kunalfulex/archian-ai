import json
import re
from typing import Optional, Dict, Any, List
import aiohttp
import ssl
import certifi
import asyncio
from datetime import datetime
from db.connection import database  # Make sure this is the correct import in your environment
from uuid import UUID, uuid4

from services.salesforce_org_rules_service import sync_org_rules_after_schema_sync


# Limit concurrent API calls
CONCURRENT_LIMIT = 5
semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)


def _create_ssl_connector() -> aiohttp.TCPConnector:
    """
    Create an aiohttp connector that trusts the system/Certifi CA bundle.
    Fixes SSLCertVerificationError on macOS/local Python where default trust
    store may not be available to asyncio/aiohttp.
    """
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    return aiohttp.TCPConnector(ssl=ssl_context)

def _new_session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(connector=_create_ssl_connector())


# ✅ Get valid access token from DB → refresh if expired
async def get_valid_salesforce_token(
    user_id: str,
    org_id: Optional[str] = None,
    connected_app_id: Optional[int] = None,
    force_refresh: bool = False,
) -> Optional[dict]:
    # Fetch existing token details
    base_query = """
        SELECT *
        FROM salesforce_tokens
        WHERE user_id = :user_id
    """
    params: Dict[str, Any] = {"user_id": user_id}

    if connected_app_id is not None:
        base_query += " AND connected_app_id = :connected_app_id"
        params["connected_app_id"] = connected_app_id

    if org_id:
        base_query += " AND org_id = :org_id"
        params["org_id"] = org_id

    query = base_query + " ORDER BY issued_at DESC LIMIT 1"

    token_row = await database.fetch_one(query, values=params)

    if not token_row:
        raise Exception("Salesforce token not found for user.")

    token = dict(token_row)

    access_token = token["access_token"]
    refresh_token = token["refresh_token"]
    issued_at = token["issued_at"]
    token_type = token["token_type"]
    instance_url = token["instance_url"]

    # Refresh if token is older than 55 minutes OR if the caller explicitly requests a refresh
    # (force_refresh=True is used after a 401 to recover from server-side revocation/expiry)
    if force_refresh or (datetime.utcnow() - issued_at).total_seconds() > 3300:  # 55 minutes
        # Fetch connected app credentials (environment_type is on salesforce_tokens, not connected_apps)
        connected_app_query = """
            SELECT client_id, client_secret
            FROM salesforce_connected_apps
            WHERE user_id = :user_id
        """
        connected_app_params = {"user_id": user_id}
        if token.get("connected_app_id"):
            connected_app_query += " AND id = :connected_app_id"
            connected_app_params["connected_app_id"] = token["connected_app_id"]

        connected_app = await database.fetch_one(
            connected_app_query + " ORDER BY created_at DESC LIMIT 1",
            values=connected_app_params,
        )

        if not connected_app:
            raise Exception("No Connected App found for user.")

        # environment_type lives on salesforce_tokens (source of truth per org)
        environment = token.get("environment_type") or "production"
        token_url = "https://login.salesforce.com/services/oauth2/token"
        if environment == "sandbox":
            token_url = "https://test.salesforce.com/services/oauth2/token"

        refresh_payload = {
            "grant_type": "refresh_token",
            "client_id": connected_app["client_id"],
            "client_secret": connected_app["client_secret"],
            "refresh_token": refresh_token
        }

        try:
            async with _new_session() as session:
                async with session.post(token_url, data=refresh_payload) as response:
                    error_text = await response.text()
                    if response.status >= 400:
                        # Mirror the detailed error handling from the previous implementation
                        status_code = response.status
                        
                        if status_code == 400:
                            if "unknown_error" in error_text or "retry your request" in error_text:
                                raise Exception("Salesforce connection expired. Please check your OAuth scopes in Salesforce and reconnect your org from the Connections tab.")
                            elif "invalid_grant" in error_text:
                                print(f"⚠️ [TOKEN REFRESH] Salesforce returned invalid_grant. Token URL: {token_url}, org_id: {token.get('org_id')}")
                                raise Exception("Salesforce refresh token expired or revoked. Please reconnect your org from the Connections tab (Connect via SSO or your Connected App).")
                            elif "invalid_client" in error_text:
                                raise Exception("Invalid Salesforce Connected App credentials. Please check your Connected App settings and reconnect from the Connections tab.")
                            else:
                                raise Exception("Invalid Salesforce credentials. Please reconnect your org from the Connections tab.")
                        elif status_code == 401:
                            raise Exception("Salesforce authentication failed. Please reconnect your org from the Connections tab.")
                        elif status_code == 429:
                            raise Exception("Salesforce rate limit exceeded. Please try again in a few minutes.")
                        else:
                            raise Exception(f"Salesforce authentication failed (Error {status_code}). Please reconnect your org from the Connections tab.")
                    
                    # Successful refresh
                    refreshed = await response.json()

            # Update token in DB
            access_token = refreshed["access_token"]

            if token.get("id"):
                await database.execute(
                    """
                    UPDATE salesforce_tokens
                    SET access_token = :access_token, issued_at = :issued_at
                    WHERE id = :token_id
                    """,
                    values={
                        "access_token": access_token,
                        "issued_at": datetime.utcnow(),
                        "token_id": token["id"],
                    }
                )
            else:
                update_query = """
                    UPDATE salesforce_tokens
                    SET access_token = :access_token, issued_at = :issued_at
                    WHERE user_id = :user_id
                """
                update_values = {
                    "access_token": access_token,
                    "issued_at": datetime.utcnow(),
                    "user_id": user_id,
                }
                if token.get("org_id"):
                    update_query += " AND org_id = :org_id"
                    update_values["org_id"] = token["org_id"]

                await database.execute(update_query, values=update_values)

        except Exception as e:
            # Preserve high-level error message for callers
            raise

    return {
        "access_token": access_token,
        "instance_url": instance_url,
        "org_id": token.get("org_id"),
        "connected_app_id": token.get("connected_app_id"),
        "refresh_token": refresh_token,
        "token_type": token_type,
        "issued_at": issued_at,
        "token_id": token.get("id"),
        "id": token.get("id"),
    }

def format_soql_value(value):
    """
    Format a value for SOQL query based on its Python type.
    
    This function preserves data types from transformed data:
    - Numbers (int/float): unquoted (e.g., 100, 100.50)
    - Booleans: unquoted (e.g., true, false)
    - Date strings (YYYY-MM-DD): unquoted (e.g., 2026-08-12)
    - Everything else (strings, IDs): quoted with escaping (e.g., 'Medical leave')
    
    Args:
        value: The value to format (can be int, float, bool, str, etc.)
    
    Returns:
        Formatted string ready for SOQL query
    """
    # Numbers: preserve as unquoted numeric values
    if isinstance(value, (int, float)):
        return str(value)
    
    # Booleans: convert to lowercase unquoted string
    if isinstance(value, bool):
        return 'true' if value else 'false'
    
    # Convert to string for pattern matching
    value_str = str(value)
    
    # Date strings: Check for YYYY-MM-DD pattern (from transformed data)
    # This is the format returned by transform_value_for_destination for date fields
    date_pattern = r'^\d{4}-\d{2}-\d{2}$'
    if re.match(date_pattern, value_str):
        return value_str  # Date: unquoted (Salesforce requirement)
    
    # Everything else (strings, IDs, text): quote and escape single quotes
    escaped_value = value_str.replace("'", "\\'")
    return f"'{escaped_value}'"


# Query record in Salesforce to check if it exists
async def query_existing_salesforce_record(access_token, instance_url, object_name, fields):
    query_clauses = [f"{key} = {format_soql_value(value)}" for key, value in fields.items()]
    query = f"SELECT Id FROM {object_name} WHERE {' AND '.join(query_clauses)} LIMIT 1"
    url = f"{instance_url}/services/data/v59.0/query"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"q": query}
    
    async with _new_session() as session:
        async with session.get(url, headers=headers, params=params) as response:
            if response.status == 200:
                result = await response.json()
                
                if result.get("records"):
                    return result["records"][0]
            else:
                # Try to read error response
                try:
                    error_body = await response.text()
                    print(f"❌ Query error for {object_name}: HTTP {response.status}, Response: {error_body}")
                except:
                    print(f"❌ Query error for {object_name}: HTTP {response.status}")
            return None

# Create a new record in Salesforce
async def create_salesforce_record(access_token, instance_url, object_name, fields):
    url = f"{instance_url}/services/data/v59.0/sobjects/{object_name}/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    async with _new_session() as session:
        async with session.post(url, headers=headers, json=fields) as response:
            response_data = await response.json()
            
            # Check if response indicates an error
            if response.status >= 400:
                # Handle error response - could be list or dict
                if isinstance(response_data, list) and len(response_data) > 0:
                    # Salesforce error response is a list
                    error_info = response_data[0]
                    error_message = error_info.get('message', 'Unknown Salesforce error')
                    error_code = error_info.get('errorCode', 'UNKNOWN_ERROR')
                    fields_with_errors = error_info.get('fields', [])
                    
                    raise Exception(f"Salesforce Error ({error_code}): {error_message}. Fields with issues: {fields_with_errors}")
                elif isinstance(response_data, dict):
                    # Salesforce error response is a dict
                    error_message = response_data.get('message', 'Unknown Salesforce error')
                    error_code = response_data.get('errorCode', 'UNKNOWN_ERROR')
                    fields_with_errors = response_data.get('fields', [])
                    
                    raise Exception(f"Salesforce Error ({error_code}): {error_message}. Fields with issues: {fields_with_errors}")
                else:
                    # Fallback error handling
                    raise Exception(f"Salesforce API Error: {response_data}")
            
            # Success response - should be a dict with id
            if isinstance(response_data, dict) and response_data.get('id'):
                return response_data
            elif isinstance(response_data, dict):
                # Some Salesforce responses don't include id in certain cases
                return response_data
            else:
                # Unexpected response format
                raise Exception(f"Unexpected Salesforce response format: {response_data}")

# Update an existing record in Salesforce
async def update_salesforce_record(access_token, instance_url, object_name, record_id, fields):
    url = f"{instance_url}/services/data/v59.0/sobjects/{object_name}/{record_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    async with _new_session() as session:
        async with session.patch(url, headers=headers, json=fields) as response:
            
            # Check if response indicates an error
            if response.status >= 400:
                # Try to get error details from response
                try:
                    response_data = await response.json()
                    
                    # Handle error response - could be list or dict
                    if isinstance(response_data, list) and len(response_data) > 0:
                        # Salesforce error response is a list
                        error_info = response_data[0]
                        error_message = error_info.get('message', 'Unknown Salesforce error')
                        error_code = error_info.get('errorCode', 'UNKNOWN_ERROR')
                        fields_with_errors = error_info.get('fields', [])
                        
                        raise Exception(f"Salesforce Error ({error_code}): {error_message}. Fields with issues: {fields_with_errors}")
                    elif isinstance(response_data, dict):
                        # Salesforce error response is a dict
                        error_message = response_data.get('message', 'Unknown Salesforce error')
                        error_code = response_data.get('errorCode', 'UNKNOWN_ERROR')
                        fields_with_errors = response_data.get('fields', [])
                        
                        raise Exception(f"Salesforce Error ({error_code}): {error_message}. Fields with issues: {fields_with_errors}")
                    else:
                        # Fallback error handling
                        raise Exception(f"Salesforce API Error: {response_data}")
                except:
                    # If we can't parse JSON, use text response
                    error_text = await response.text()
                    raise Exception(f"Salesforce API Error (Status {response.status}): {error_text}")
            
            # Success response for PATCH requests
            if response.status == 204:  # No content for successful updates
                return {"status": "success", "id": record_id}
            else:
                # Try to get response data for other success statuses
                try:
                    response_data = await response.json()
                    return response_data
                except:
                    return {"status": "success", "id": record_id}

# Delete a record in Salesforce
async def delete_salesforce_record(access_token, instance_url, object_name, record_id):
    """
    Delete a single Salesforce record using REST API.
    
    Args:
        access_token: Salesforce access token
        instance_url: Salesforce instance URL
        object_name: Salesforce object name (e.g., "Account", "Contact")
        record_id: Salesforce record ID to delete
        
    Returns:
        Dict with success status
        
    Raises:
        Exception if deletion fails
    """
    url = f"{instance_url}/services/data/v59.0/sobjects/{object_name}/{record_id}"
    headers = {
        "Authorization": f"Bearer {access_token}"
    }
    
    async with _new_session() as session:
        async with session.delete(url, headers=headers) as response:
            # Check if response indicates an error
            if response.status >= 400:
                # Try to get error details from response
                try:
                    response_data = await response.json()
                    
                    # Handle error response - could be list or dict
                    if isinstance(response_data, list) and len(response_data) > 0:
                        error_info = response_data[0]
                        error_message = error_info.get('message', 'Unknown Salesforce error')
                        error_code = error_info.get('errorCode', 'UNKNOWN_ERROR')
                        
                        # If record not found (already deleted), treat as success
                        if error_code == 'NOT_FOUND':
                            return {"success": True, "id": record_id, "message": "Record already deleted"}
                        
                        raise Exception(f"Salesforce Error ({error_code}): {error_message}")
                    elif isinstance(response_data, dict):
                        error_message = response_data.get('message', 'Unknown Salesforce error')
                        error_code = response_data.get('errorCode', 'UNKNOWN_ERROR')
                        
                        # If record not found (already deleted), treat as success
                        if error_code == 'NOT_FOUND':
                            return {"success": True, "id": record_id, "message": "Record already deleted"}
                        
                        raise Exception(f"Salesforce Error ({error_code}): {error_message}")
                    else:
                        raise Exception(f"Salesforce API Error: {response_data}")
                except Exception as e:
                    # If we can't parse the error, check status code
                    if response.status == 404:
                        # Record not found (already deleted), treat as success
                        return {"success": True, "id": record_id, "message": "Record already deleted"}
                    raise Exception(f"Salesforce API Error: HTTP {response.status}")
            
            # Success response - 204 No Content for successful deletions
            if response.status == 204:
                return {"success": True, "id": record_id}
            else:
                # Try to parse response if available
                try:
                    response_data = await response.json()
                    return response_data
                except:
                    return {"success": True, "id": record_id}


async def fetch_and_store_salesforce_objects(
    user_id: UUID,
    instance_url: str,
    access_token: str,
    org_id: str,
    debug: bool = False,
    *,
    skip_org_rules_sync: bool = False,
    schedule_labeling: bool = True,
):
    """
    Fetches all creatable/updatable Salesforce objects for a given org and stores only relevant ones in DB.
    Filters out system, metadata, and managed package clutter objects.
    """
    token_row = await database.fetch_one(
        """
        SELECT id::text AS connector_id FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY issued_at DESC LIMIT 1
        """,
        {"user_id": str(user_id), "org_id": org_id},
    )
    connector_id = dict(token_row).get("connector_id") if token_row else None
    if connector_id:
        try:
            from services.labeling.readiness_service import on_metadata_sync_started

            await on_metadata_sync_started(
                user_id=str(user_id),
                connector_type="salesforce",
                connector_ref={"connector_id": connector_id, "org_id": org_id},
            )
        except Exception as exc:
            print(f"⚠️ [Salesforce schema] Readiness sync-start hook failed: {exc}")

    url = f"{instance_url}/services/data/v59.0/sobjects"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with _new_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                raise Exception(f"Failed to fetch objects: {await response.text()}")
            metadata = await response.json()

    objects = metadata.get("sobjects", [])
    now = datetime.utcnow()

    # Previous schema counts for delta billing (charge only for new objects/fields on reconnect)
    prev_obj_count = 0
    prev_fld_count = 0
    try:
        obj_row = await database.fetch_one(
            """SELECT COUNT(*) AS c FROM salesforce_objects WHERE user_id = :user_id AND org_id = :org_id""",
            values={"user_id": str(user_id), "org_id": org_id},
        )
        fld_row = await database.fetch_one(
            """SELECT COUNT(*) AS c FROM salesforce_object_fields WHERE user_id = :user_id AND org_id = :org_id""",
            values={"user_id": str(user_id), "org_id": org_id},
        )
        prev_obj_count = int(obj_row["c"]) if obj_row else 0
        prev_fld_count = int(fld_row["c"]) if fld_row else 0
    except Exception as e:
        print(f"⚠️ [Salesforce schema] Could not load previous counts for delta: {e}")

    # --- Filters ---
    EXCLUDED_LABEL_KEYWORDS = [
        "Log", "Trace", "Cache", "Scan", "Token", "Key", "Definition", "Stage", "Step",
        "Email Relay", "Relay", "Notification", "License", "Permission", "Metric", "Orchestration",
        "Policy", "Baseline", "RTBF", "Session", "Audit", "Credential", "Domain",
        "Custom Scope", "Prompt", "Theme", "SAML", "Assistant", "Analytics", "Encryption", "Profile",
        "AppMenuItem", "Brand", "Message", "Chat", "Email", "Feed", "Streaming", "Macro",
        "Wave", "Lightning", "ML", "Push", "Setup", "Telemetry", "Experiment", "Component",
        "Help", "Trusted", "Schedule", "Scorecard", "Version", "WhiteList", "OAuth", "Provisioning"
    ]

    EXCLUDED_NAME_SUFFIXES = [
        "History", "Share", "ChangeEvent", "Feed", "Tag", "Record", "Item", "Relation"
    ]

    EXACT_NAME_EXCLUSIONS = {
        "ApexClass", "ApexPage", "ApexTrigger", "Scontrol", "StaticResource", "UserPreference",
        "AppUsageAssignment", "ContentAsset", "Note", "RecentlyViewed", "UserAppInfo", "UserAppMenuCustomization", "UserDefinedLabel",
        "UserDefinedLabelAssignment", "UserListView", "UserListViewCriterion", "UserRole", "Vote", "WebLink", "WorkAccess", "WorkBadge", "WorkBadgeDefinition", "WorkThanks",
        "TodayGoal", "Topic", "TopicAssignment", "TableauHostMapping", "Solution", "SearchPromotionRule", "SiteRedirectMapping", "RecommendationResponse", "RecordAction", "RecordType", "RedirectWhitelistUrl",
        "QueueSobject", "ProcessExceptionEvent", "ProcessInstanceWorkitem", "PipelineInspectionListView", "OrgDeleteRequest", "MobileApplicationDetail", "OpportunityCompetitor", "MobileApplicationDetail", "MailmergeTemplate",
        "AccountCleanInfo", "AccountCleanInfo", "AccountContactRole", "AccountPartner", "ActionLinkGroupTemplate", "ActionLinkTemplate", "AdditionalNumber", "AuthConfigProviders", "AuthProvider", "BrandTemplate", "BriefcaseAssignment",
        "BusinessHours", "BusinessProcess", "CalendarView", "CallCenter", "CallCoachingMediaProvider"
    }

    # INCLUDE_IF_LABEL_CONTAINS = [
    #     "Appointment"
    # ]

    count = 0
    object_jobs: list[tuple[str, str]] = []

    for obj in objects:
        label = obj.get("label", "")
        name = obj.get("name", "")

        if not obj.get("createable") and not obj.get("updateable"):
            continue
        if not label or not name:
            continue

        # ⛔ Skip exact known object names like Apex, Scontrol, etc.
        if name in EXACT_NAME_EXCLUSIONS:
            continue

        # ⛔ Skip by label keyword clutter
        if any(keyword.lower() in label.lower() for keyword in EXCLUDED_LABEL_KEYWORDS):
            continue

        # ⛔ Skip by suffixes
        if any(name.endswith(suffix) for suffix in EXCLUDED_NAME_SUFFIXES):
            continue

        # ⛔ Skip namespaced objects unless label is useful
        # if "__" in name and not any(keyword.lower() in label.lower() for keyword in INCLUDE_IF_LABEL_CONTAINS):
        #     if debug:
        #         print(f"⛔ Skipping namespaced object: {name} ({label})")
        #     continue

        # ✅ Passed all filters, insert into DB
        await database.execute(
            """
            INSERT INTO salesforce_objects (
                user_id,
                org_id,
                object_api_name,
                label,
                description,
                is_custom,
                is_creatable,
                is_updatable,
                is_retrieveable,
                is_deletable,
                created_at,
                uuid_id
            )
            VALUES (
                :user_id, :org_id, :object_api_name, :label, :description,
                :is_custom, :is_creatable, :is_updatable,
                :is_retrieveable, :is_deletable, :created_at,
                gen_random_uuid()
            )
            ON CONFLICT (user_id, org_id, object_api_name) DO UPDATE SET
                label = EXCLUDED.label,
                description = EXCLUDED.description,
                is_custom = EXCLUDED.is_custom,
                is_creatable = EXCLUDED.is_creatable,
                is_updatable = EXCLUDED.is_updatable,
                is_retrieveable = EXCLUDED.is_retrieveable,
                is_deletable = EXCLUDED.is_deletable,
                created_at = EXCLUDED.created_at
            """,
            values={
                "user_id": user_id,
                "org_id": org_id,
                "object_api_name": name,
                "label": label,
                "description": label,
                "is_custom": obj.get("custom", False),
                "is_creatable": obj.get("createable", False),
                "is_updatable": obj.get("updateable", False),
                "is_retrieveable": obj.get("retrieveable", False),
                "is_deletable": obj.get("deletable", False),
                "created_at": now,
            }
        )


        object_jobs.append((name, label))
        count += 1

    if connector_id and count > 0:
        try:
            from services.labeling.readiness_service import report_metadata_sync_progress

            await report_metadata_sync_progress(
                user_id=str(user_id),
                connector_type="salesforce",
                connector_ref={"connector_id": connector_id, "org_id": org_id},
                processed=0,
                total=count,
            )
        except Exception as progress_exc:
            print(f"⚠️ [Salesforce schema] Progress init failed: {progress_exc}")

    progress_lock = asyncio.Lock()
    processed_count = 0
    last_flush_at = 0.0

    async def _flush_object_progress(*, force: bool = False) -> None:
        nonlocal last_flush_at
        if not connector_id or count <= 0:
            return
        import time

        async with progress_lock:
            now = time.monotonic()
            if not force and now - last_flush_at < 1.0 and processed_count % 5 != 0:
                return
            last_flush_at = now
            try:
                from services.labeling.readiness_service import report_metadata_sync_progress

                await report_metadata_sync_progress(
                    user_id=str(user_id),
                    connector_type="salesforce",
                    connector_ref={"connector_id": connector_id, "org_id": org_id},
                    processed=processed_count,
                    total=count,
                )
            except Exception as progress_exc:
                print(f"⚠️ [Salesforce schema] Progress update failed: {progress_exc}")

    async def fetch_one_object(object_name: str, object_label: str) -> None:
        nonlocal processed_count
        try:
            await fetch_fields_with_throttle(
                object_name,
                object_label,
                instance_url,
                access_token,
                org_id,
                user_id,
                debug,
            )
        finally:
            processed_count += 1
            await _flush_object_progress()

    tasks = [
        fetch_one_object(name, label)
        for name, label in object_jobs
    ]

    # Run all field tasks concurrently but within semaphore limit
    await asyncio.gather(*tasks)
    await _flush_object_progress(force=True)

    try:
        from services.connector_metadata.empty_catalog_cleanup import (
            purge_salesforce_objects_without_fields,
        )

        removed = await purge_salesforce_objects_without_fields(str(user_id), org_id)
        if removed and debug:
            print(f"⛔ Removed {removed} Salesforce object(s) with no stored fields")
    except Exception as exc:
        print(f"⚠️ [Salesforce schema] Empty-object cleanup failed: {exc}")

    # Validation + duplicate/matching rules per object (skipped when CMA runs drift-only rules sync)
    if not skip_org_rules_sync:
        try:
            await sync_org_rules_after_schema_sync(
                user_id, org_id, instance_url, access_token
            )
        except Exception as e:
            print(f"⚠️ [Salesforce org rules] Sync failed (non-fatal): {e}")

    # Charge credits only for new objects/fields (delta) on reconnect; skip if unlimited
    try:
        from services.entitlements import get_entitlements
        from utils.credit_config import compute_connector_schema_credits
        user_plan = await get_entitlements().get_user_plan(str(user_id))
        credits_per_month = user_plan.get("credits_per_month") if user_plan else None
        if credits_per_month is not None and credits_per_month != -1:
            obj_row = await database.fetch_one(
                """SELECT COUNT(*) AS c FROM salesforce_objects WHERE user_id = :user_id AND org_id = :org_id""",
                values={"user_id": str(user_id), "org_id": org_id},
            )
            fld_row = await database.fetch_one(
                """SELECT COUNT(*) AS c FROM salesforce_object_fields WHERE user_id = :user_id AND org_id = :org_id""",
                values={"user_id": str(user_id), "org_id": org_id},
            )
            curr_obj_count = int(obj_row["c"]) if obj_row else 0
            curr_fld_count = int(fld_row["c"]) if fld_row else 0
            new_objects = max(0, curr_obj_count - prev_obj_count)
            new_fields = max(0, curr_fld_count - prev_fld_count)
            if new_objects > 0 or new_fields > 0:
                # Schema analysis is free; no credit deduction
                print(f"✅ [Salesforce schema] Schema delta: {new_objects} new objects, {new_fields} new fields (no charge)")
            else:
                print(f"✅ [Salesforce schema] No schema delta; no credits charged")
    except Exception as e:
        print(f"⚠️ [Salesforce schema] Failed to deduct schema credits: {e}")

    token_row = await database.fetch_one(
        """
        SELECT id::text AS connector_id FROM salesforce_tokens
        WHERE user_id = :user_id AND org_id = :org_id
        ORDER BY issued_at DESC LIMIT 1
        """,
        {"user_id": str(user_id), "org_id": org_id},
    )
    connector_id = dict(token_row).get("connector_id") if token_row else None

    if schedule_labeling and connector_id:
        try:
            from services.connector_metadata.empty_catalog_cleanup import (
                purge_salesforce_objects_without_fields,
            )
            from services.labeling.catalog_hash_helpers import (
                salesforce_legacy_uncategorized_keys,
                update_salesforce_object_hashes,
            )
            from services.labeling.pending_labeling import (
                schedule_pending_entity_labeling_fire_and_forget,
            )

            await purge_salesforce_objects_without_fields(str(user_id), org_id)
            current_hashes = await update_salesforce_object_hashes(str(user_id), org_id)
            legacy_keys = await salesforce_legacy_uncategorized_keys(str(user_id), org_id)
            schedule_pending_entity_labeling_fire_and_forget(
                user_id=str(user_id),
                connector_type="salesforce",
                connector_ref={"connector_id": connector_id, "org_id": org_id},
                current_hashes=current_hashes,
                drift_keys=[],
                legacy_keys=legacy_keys,
            )
        except Exception as labeling_exc:
            print(f"⚠️ [Salesforce schema] Labeling schedule failed: {labeling_exc}")

    try:
        from services.labeling.readiness_service import on_metadata_sync_complete

        if connector_id:
            await on_metadata_sync_complete(
                user_id=str(user_id),
                connector_type="salesforce",
                connector_ref={"connector_id": connector_id, "org_id": org_id},
            )
    except Exception as exc:
        print(f"⚠️ [Salesforce schema] Readiness hook failed: {exc}")



async def fetch_fields_with_throttle(object_name, label, instance_url, access_token, org_id, user_id, debug):
    async with semaphore:
        try:
            await fetch_and_store_object_fields(
                object_api_name=object_name,
                object_label=label,
                instance_url=instance_url,
                access_token=access_token,
                org_id=org_id,
                user_id=user_id,
                debug=debug
            )
        except Exception as e:
            print(f"⚠️ Failed to fetch fields for {object_name}: {str(e)}")


async def fetch_and_store_object_fields(
    object_api_name: str,
    object_label: str,
    instance_url: str,
    access_token: str,
    org_id: str,
    user_id: UUID,
    debug: bool = False,
):
    """
    Fetches field metadata for a given Salesforce object and stores it in the database.
    """
    url = f"{instance_url}/services/data/v59.0/sobjects/{object_api_name}/describe"
    headers = {"Authorization": f"Bearer {access_token}"}

    async with _new_session() as session:
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                print(f"❌ Failed to fetch fields for {object_api_name}: {await response.text()}")
                return
            describe_result = await response.json()

    fields = describe_result.get("fields", [])
    now = datetime.utcnow()
    saved_count = 0

    for field in fields:
        field_name = field.get("name", "")
        field_label = field.get("label", "")
        data_type = field.get("type", "")
        is_custom = field.get("custom", False)
        # Boolean fields are never required (even if nillable is false)
        # For other data types, if nillable is false, the field is required
        is_required = False if data_type == "boolean" else not field.get("nillable", True)
        is_lookup = bool(field.get("referenceTo"))
        reference_to = field.get("referenceTo", [])
        is_updatable = field.get("updateable", True)
        is_creatable = field.get("createable", True)
        is_defaulted_on_create = field.get("defaultedOnCreate", True)
        field_description = field.get("description") or field.get("inlineHelpText")

        if not is_creatable or not field_name or not data_type:
            continue

        await database.execute(
            """
            INSERT INTO salesforce_object_fields (
                user_id,
                org_id,
                object_api_name,
                object_label,
                field_api_name,
                field_label,
                data_type,
                is_custom,
                is_required,
                is_lookup,
                lookup_object_api_name,
                relationship_name,
                is_updatable,
                is_creatable,
                field_description,
                created_at,
                is_defaulted_on_create,
                uuid_id
            )
            VALUES (
                :user_id, :org_id, :object_api_name, :object_label,
                :field_api_name, :field_label, :data_type,
                :is_custom, :is_required, :is_lookup,
                :lookup_object_api_name, :relationship_name, :is_updatable, :is_creatable,
                :field_description, :created_at, :is_defaulted_on_create,
                gen_random_uuid()
            )
            ON CONFLICT (user_id, org_id, object_api_name, field_api_name) DO UPDATE SET
                field_label = EXCLUDED.field_label,
                data_type = EXCLUDED.data_type,
                is_custom = EXCLUDED.is_custom,
                is_required = EXCLUDED.is_required,
                is_lookup = EXCLUDED.is_lookup,
                lookup_object_api_name = EXCLUDED.lookup_object_api_name,
                relationship_name = EXCLUDED.relationship_name,
                is_updatable = EXCLUDED.is_updatable,
                is_creatable = EXCLUDED.is_creatable,
                field_description = EXCLUDED.field_description,
                created_at = EXCLUDED.created_at,
                is_defaulted_on_create = EXCLUDED.is_defaulted_on_create
            """,
            values={
                "user_id": user_id,
                "org_id": org_id,
                "object_api_name": object_api_name,
                "object_label": object_label,
                "field_api_name": field_name,
                "field_label": field_label,
                "data_type": data_type,
                "is_custom": is_custom,
                "is_required": is_required,
                "is_lookup": is_lookup,
                "lookup_object_api_name": reference_to[0] if reference_to else None,
                "relationship_name": field.get("relationshipName"),
                "is_updatable": is_updatable,
                "is_creatable": is_creatable,
                "field_description": field_description,
                "created_at": now,
                "is_defaulted_on_create": is_defaulted_on_create
            }
        )

        saved_count += 1

    if saved_count == 0:
        from services.connector_metadata.empty_catalog_cleanup import (
            purge_salesforce_object_catalog_entry,
        )

        await purge_salesforce_object_catalog_entry(
            str(user_id), org_id, object_api_name
        )
        if debug:
            print(f"⛔ Removed {object_api_name} — no storable creatable fields")
        return

    # Store record type information from describe result
    record_type_infos = describe_result.get("recordTypeInfos", [])
    non_master_record_types = [rt for rt in record_type_infos if not rt.get("master", False)]

    if non_master_record_types:
        # Fetch the uuid_id for this salesforce_objects row to use as FK
        obj_row = await database.fetch_one(
            """
            SELECT uuid_id FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
            """,
            values={"user_id": str(user_id), "org_id": org_id, "object_api_name": object_api_name},
        )
        salesforce_object_uuid = obj_row["uuid_id"] if obj_row else None

        for rt in non_master_record_types:
            await database.execute(
                """
                INSERT INTO salesforce_object_record_types (
                    salesforce_object_uuid,
                    user_id,
                    org_id,
                    object_api_name,
                    record_type_id,
                    name,
                    developer_name,
                    is_active,
                    is_default,
                    is_master
                )
                VALUES (
                    :salesforce_object_uuid, :user_id, :org_id, :object_api_name,
                    :record_type_id, :name, :developer_name,
                    :is_active, :is_default, :is_master
                )
                ON CONFLICT (user_id, org_id, object_api_name, record_type_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    developer_name = EXCLUDED.developer_name,
                    is_active = EXCLUDED.is_active,
                    is_default = EXCLUDED.is_default,
                    salesforce_object_uuid = EXCLUDED.salesforce_object_uuid
                """,
                values={
                    "salesforce_object_uuid": salesforce_object_uuid,
                    "user_id": str(user_id),
                    "org_id": org_id,
                    "object_api_name": object_api_name,
                    "record_type_id": rt.get("recordTypeId", ""),
                    "name": rt.get("name", ""),
                    "developer_name": rt.get("developerName"),
                    "is_active": rt.get("active", True),
                    "is_default": rt.get("defaultRecordTypeMapping", False),
                    "is_master": False,
                },
            )

        await database.execute(
            """
            UPDATE salesforce_objects
            SET has_record_types = true
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
            """,
            values={"user_id": str(user_id), "org_id": org_id, "object_api_name": object_api_name},
        )
        print(f"✅ Stored {len(non_master_record_types)} record type(s) for {object_api_name}")



async def categorize_salesforce_objects_in_batches_with_notification(user_id: UUID, org_id: str, batch_size: int = 10):
    from services.architect_agent import categorize_salesforce_objects_in_batches

    try:

        total_categorized = 0

        while True:
            # Run a single batch
            categorized_in_this_batch = await categorize_salesforce_objects_in_batches(user_id=user_id, org_id=org_id, batch_size=batch_size)
            
            if categorized_in_this_batch == 0:
                # ✅ Notify user
                await database.execute(
                    """
                    INSERT INTO user_notifications (user_id, title, message, created_at)
                    VALUES (:user_id, :title, :message, NOW())
                    """,
                    values={
                        "user_id": user_id,
                        "title": "Object Categorization Complete",
                        "message": f"{total_categorized} Salesforce objects for org {org_id} have been categorized.",
                    }
                )

                # Fetch user's email from DB
                user = await database.fetch_one("SELECT email FROM users WHERE id = :user_id", {"user_id": user_id})
                if user:
                    # from utils.email_utils import send_categorization_complete_email # This import is now local
                    from utils.email_utils import send_categorization_complete_email
                    await send_categorization_complete_email(to_email=user["email"])
                
                # Mark categorization as completed in salesforce_tokens table
                await database.execute(
                    """
                    UPDATE salesforce_tokens 
                    SET categorization_completed = TRUE
                    WHERE org_id = :org_id AND user_id = :user_id
                    """,
                    values={"org_id": org_id, "user_id": str(user_id)}
                )
                
                break  # Exit when no more objects to categorize
            
            total_categorized += categorized_in_this_batch

        


    except Exception as e:
        print("❌ Background categorization failed:", e)



