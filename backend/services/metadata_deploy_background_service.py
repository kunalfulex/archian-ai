"""
Background metadata deploy: deploy Salesforce custom fields from source org to destination org.
Creates a migration_batch (batch_type='metadata'), processes each field via Tooling API,
and writes one migration_record per field (source_data = field metadata, salesforce_results = id/action/view_link).
"""

import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional

from services.migration_records_service import MigrationRecordsService
from services.entitlements import get_entitlements
from utils.redis_service import migration_job_store
from utils.async_job_store import AsyncJobStore
from utils.salesforceapimigration import get_valid_salesforce_token
from utils.salesforce_tooling_api import (
    get_custom_field_metadata,
    create_or_update_custom_field,
    get_field_permissions_for_field,
    set_field_permissions_from_grants,
    set_default_field_level_security,
    get_record_types_for_object,
    create_or_update_record_type,
    get_custom_object_metadata,
    _full_name,
)
from utils.salesforce_metadata_api import create_custom_object_via_soap


def _custom_field_view_link(instance_url: str, field_id: str) -> str:
    """Build Setup view link for a CustomField in Salesforce (Lightning)."""
    base = (instance_url or "").rstrip("/")
    if not base or not field_id:
        return ""
    return f"{base}/lightning/setup/CustomField/page?address=%2F{field_id}"


def _record_type_view_link(instance_url: str, object_api_name: str, record_type_id: str) -> str:
    """Build Setup view link for a RecordType in Salesforce (Lightning).
    Links to the RecordType detail page under the object's setup page."""
    base = (instance_url or "").rstrip("/")
    if not base or not record_type_id:
        return ""
    return f"{base}/lightning/setup/ObjectManager/{object_api_name}/RecordTypes/{record_type_id}/view"


def _custom_object_view_link(instance_url: str, object_api_name: str) -> str:
    """Build Setup view link for a CustomObject in Salesforce (Lightning Object Manager)."""
    base = (instance_url or "").rstrip("/")
    if not base or not object_api_name:
        return ""
    return f"{base}/lightning/setup/ObjectManager/{object_api_name}/Details/view"


class MetadataDeployBackgroundService:
    """Run metadata (field) deploy in background and write migration_records."""

    def __init__(self):
        self.job_store = AsyncJobStore(migration_job_store)

    async def start_metadata_deploy_job(
        self,
        batch_id: str,
        user_id: str,
        object_api_name: str,
        field_api_names: List[str],
        source_org_id: str,
        dest_org_id: str,
        source_connected_app_id: Optional[int] = None,
        dest_connected_app_id: Optional[int] = None,
        source_conn_name: Optional[str] = None,
        dest_conn_name: Optional[str] = None,
        include_record_types: bool = False,
        object_exists_on_dest: bool = True,
    ) -> str:
        """
        Enqueue a metadata deploy job. The batch must already exist.
        Returns job_id for tracking (caller can show batch_id to user for viewing records).
        When include_record_types=True, record types are deployed after all fields.
        When object_exists_on_dest=False, the custom object is created on destination first.
        """
        job_id = str(uuid.uuid4())
        await self.job_store.set(job_id, {
            "batch_id": batch_id,
            "user_id": user_id,
            "status": "running",
            "type": "metadata_deploy",
            "total_records": len(field_api_names),
            "processed_records": 0,
            "successful_records": 0,
            "failed_records": 0,
            "started_at": datetime.utcnow().isoformat(),
            "object_api_name": object_api_name,
        })
        asyncio.create_task(
            self._process_metadata_deploy_job(
                job_id=job_id,
                batch_id=batch_id,
                user_id=user_id,
                object_api_name=object_api_name,
                field_api_names=field_api_names,
                source_org_id=source_org_id,
                dest_org_id=dest_org_id,
                source_connected_app_id=source_connected_app_id,
                dest_connected_app_id=dest_connected_app_id,
                include_record_types=include_record_types,
                object_exists_on_dest=object_exists_on_dest,
            )
        )
        return job_id

    async def _process_metadata_deploy_job(
        self,
        job_id: str,
        batch_id: str,
        user_id: str,
        object_api_name: str,
        field_api_names: List[str],
        source_org_id: str,
        dest_org_id: str,
        source_connected_app_id: Optional[int] = None,
        dest_connected_app_id: Optional[int] = None,
        include_record_types: bool = False,
        object_exists_on_dest: bool = True,
    ) -> None:
        """Process each field: get metadata from source, create/update on dest, write migration_record.
        When object_exists_on_dest=False, creates the custom object on destination first.
        When include_record_types=True, also deploys record types after all fields are processed."""
        successful_count = 0
        failed_count = 0
        processed = 0
        created_count = 0  # Records where action='created' (1 credit per FLS permission applied)
        try:
            source_token_info = await get_valid_salesforce_token(
                user_id=str(user_id),
                org_id=source_org_id,
                connected_app_id=source_connected_app_id,
            )
            dest_token_info = await get_valid_salesforce_token(
                user_id=str(user_id),
                org_id=dest_org_id,
                connected_app_id=dest_connected_app_id,
            )
            dest_url = (dest_token_info.get("instance_url") or "").rstrip("/")
            print(f"   [METADATA_DEPLOY_JOB] Dest token identity: org_id={dest_token_info.get('org_id')}, connected_app_id={dest_token_info.get('connected_app_id')}, salesforce_tokens.id={dest_token_info.get('id') or dest_token_info.get('token_id')}, instance_url={dest_url}")
        except Exception as e:
            print(f"❌ [METADATA_DEPLOY_JOB] Failed to get tokens: {e}")
            await MigrationRecordsService.update_batch_status(
                batch_id, status="failed",
                error_summary=str(e), successful_count=0, failed_count=len(field_api_names)
            )
            await self.job_store.update(job_id, {"status": "failed", "error_summary": str(e)})
            return

        source_url = (source_token_info.get("instance_url") or "").rstrip("/")
        source_token = source_token_info.get("access_token")
        dest_url = (dest_token_info.get("instance_url") or "").rstrip("/")
        dest_token = dest_token_info.get("access_token")
        if not all([source_url, source_token, dest_url, dest_token]):
            err = "Missing source or destination token/URL"
            await MigrationRecordsService.update_batch_status(
                batch_id, status="failed", error_summary=err,
                successful_count=0, failed_count=len(field_api_names)
            )
            await self.job_store.update(job_id, {"status": "failed", "error_summary": err})
            return

        tokens_refreshed = False  # Only force-refresh once per job

        def _is_401(err_text: str) -> bool:
            return any(k in err_text for k in ("401", "Unauthorized", "INVALID_SESSION_ID", "Session expired"))

        async def _refresh_tokens() -> bool:
            nonlocal source_token, dest_token, tokens_refreshed
            if tokens_refreshed:
                return False
            try:
                new_src = await get_valid_salesforce_token(
                    user_id=str(user_id), org_id=source_org_id,
                    connected_app_id=source_connected_app_id, force_refresh=True,
                )
                new_dst = await get_valid_salesforce_token(
                    user_id=str(user_id), org_id=dest_org_id,
                    connected_app_id=dest_connected_app_id, force_refresh=True,
                )
                source_token = new_src["access_token"]
                dest_token = new_dst["access_token"]
                tokens_refreshed = True
                print(f"   [METADATA_DEPLOY_JOB] Tokens refreshed after 401")
                return True
            except Exception as re:
                print(f"   [METADATA_DEPLOY_JOB] Token refresh failed: {re}")
                return False

        # --- Deploy custom object first if it doesn't exist on destination ---
        if not object_exists_on_dest and object_api_name.endswith("__c"):
            obj_deploy_result = await self._deploy_custom_object(
                batch_id=batch_id,
                user_id=user_id,
                object_api_name=object_api_name,
                source_token=source_token,
                source_url=source_url,
                dest_token=dest_token,
                dest_url=dest_url,
                _refresh_tokens=_refresh_tokens,
                _is_401=_is_401,
            )
            if not obj_deploy_result.get("success"):
                err = obj_deploy_result.get("error", "Custom object creation failed")
                print(f"❌ [METADATA_DEPLOY_JOB] Aborting: {err}")
                await MigrationRecordsService.update_batch_status(
                    batch_id, status="failed", error_summary=err,
                    successful_count=0, failed_count=len(field_api_names),
                )
                await self.job_store.update(job_id, {"status": "failed", "error_summary": err})
                return
            # Count the object itself as 1 successful record + 1 credit
            successful_count += 1
            created_count += 1

        for idx, field_name in enumerate(field_api_names):
            job_data = await self.job_store.get(job_id)
            if job_data and job_data.get("cancelled"):
                print(f"🛑 [METADATA_DEPLOY_JOB] Job {job_id} cancelled by user")
                await self.job_store.update(job_id, {"status": "cancelled", "completed_at": datetime.utcnow().isoformat()})
                return

            full_name = _full_name(object_api_name, field_name)
            source_data = {"field_api_name": field_name, "object_api_name": object_api_name}
            record_id = None
            for field_attempt in range(2):  # attempt 0 = normal, attempt 1 = after token refresh
                try:
                    meta = await get_custom_field_metadata(
                        source_token, source_url, object_api_name, field_name
                    )
                    if not meta:
                        print(f"   ⏭️ [METADATA_DEPLOY_JOB] Skipping {field_name} (no metadata found on source)")
                        # Do not create migration_record for skipped (standard/not found) - user asked to hide skipped
                        break
                    else:
                        source_data["type"] = meta.get("type")
                        source_data["label"] = meta.get("label")
                        source_data["metadata_snapshot"] = {k: meta[k] for k in ("type", "label", "length", "required", "description") if k in meta and meta.get(k) is not None}
                        if not record_id:
                            record_id = await MigrationRecordsService.create_migration_record(
                                batch_id, str(user_id), idx, source_data, {}
                            )
                        result = await create_or_update_custom_field(
                            dest_token, dest_url, full_name, meta,
                            object_api_name=object_api_name,
                            field_api_name=field_name,
                        )
                        if result.get("success"):
                            action = result.get("action", "created")
                            fid = result.get("id")
                            view_link = _custom_field_view_link(dest_url, fid) if fid else ""
                            # Apply FLS: copy from source if any grants exist, else default (current user's profile)
                            if action == "created" and object_api_name and full_name:
                                try:
                                    grants = await get_field_permissions_for_field(
                                        source_token, source_url, object_api_name, full_name
                                    )
                                    if grants:
                                        n = await set_field_permissions_from_grants(
                                            dest_token, dest_url, object_api_name, full_name, grants
                                        )
                                        if n:
                                            print(f"      [METADATA_DEPLOY_JOB] FLS copied from source for {full_name} ({n} grant(s))")
                                    else:
                                        await set_default_field_level_security(
                                            dest_token, dest_url, object_api_name, full_name, profile_id=None
                                        )
                                except Exception as fls_err:
                                    print(f"      [METADATA_DEPLOY_JOB] FLS setup failed for {full_name}, using default: {fls_err}")
                                    await set_default_field_level_security(
                                        dest_token, dest_url, object_api_name, full_name, profile_id=None
                                    )
                            salesforce_results = {
                                "id": fid,
                                "action": action,
                                "success": True,
                                "view_link": view_link,
                            }
                            await MigrationRecordsService.update_migration_record_status(
                                record_id, status="success", salesforce_results=salesforce_results
                            )
                            successful_count += 1
                            if action == "created":
                                created_count += 1
                        else:
                            err_msg = (result.get("error") or "unknown")[:500]
                            action = result.get("action", "failed")
                            # Retry after token refresh if the failure looks like a 401
                            if _is_401(err_msg) and field_attempt == 0 and await _refresh_tokens():
                                continue
                            await MigrationRecordsService.update_migration_record_status(
                                record_id, status="failed",
                                salesforce_results={"success": False, "action": action, "error": err_msg, "view_link": ""},
                                error_message=err_msg
                            )
                            failed_count += 1
                        break  # success or non-401 failure — stop the retry loop
                except Exception as e:
                    err_str = str(e)[:500]
                    # On 401, refresh tokens and retry the field once
                    if _is_401(err_str) and field_attempt == 0 and await _refresh_tokens():
                        continue
                    if not record_id:
                        source_data["_error"] = err_str
                        record_id = await MigrationRecordsService.create_migration_record(
                            batch_id, str(user_id), idx, source_data, {}
                        )
                    await MigrationRecordsService.update_migration_record_status(
                        record_id, status="failed",
                        salesforce_results={"success": False, "action": "failed", "error": err_str, "view_link": ""},
                        error_message=err_str
                    )
                    failed_count += 1
                    break
            else:
                # meta was None (field skipped) — processed counter still increments below
                pass

            processed += 1
            if processed % 5 == 0 or processed == len(field_api_names):
                await self.job_store.update(job_id, {
                    "processed_records": processed,
                    "successful_records": successful_count,
                    "failed_records": failed_count,
                })

        # Deploy record types after all fields (if requested)
        if include_record_types:
            rt_successful, rt_failed = await self._deploy_record_types(
                batch_id=batch_id,
                user_id=user_id,
                object_api_name=object_api_name,
                source_token=source_token,
                source_url=source_url,
                dest_token=dest_token,
                dest_url=dest_url,
                dest_org_id=dest_org_id,
                field_index_offset=len(field_api_names),
            )
            successful_count += rt_successful
            failed_count += rt_failed

        total_processed = successful_count + failed_count
        await MigrationRecordsService.update_batch_status(
            batch_id, status="completed",
            successful_count=successful_count,
            failed_count=failed_count,
            total_records=total_processed,
        )
        # Update job store with final counts (including record types) before marking completed
        # so the frontend polling response reflects the true totals
        await self.job_store.update(job_id, {
            "status": "completed",
            "successful_records": successful_count,
            "failed_records": failed_count,
            "processed_records": total_processed,
            "total_records": total_processed,
        })
        print(f"✅ [METADATA_DEPLOY_JOB] Batch {batch_id}: {successful_count} ok, {failed_count} failed")

        if successful_count and user_id:
            try:
                await get_entitlements().track_successful_migration(str(user_id), successful_count)
                print(f"✅ [METADATA_DEPLOY_JOB] Usage tracking updated: {successful_count} records for user {user_id}")
            except Exception as usage_error:
                print(f"⚠️ [METADATA_DEPLOY_JOB] Usage tracking failed: {usage_error}")

        # Deduct credits: 1 per field, 1 per permission (field we applied FLS to = created fields)
        # Extraction (5) is charged at handler start before job is enqueued
        if successful_count and user_id:
            try:
                from utils.credit_config import compute_metadata_deploy_credits
                user_plan = await get_entitlements().get_user_plan(str(user_id))
                credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                if credits_per_month is not None and credits_per_month != -1:
                    deploy_credits = compute_metadata_deploy_credits(
                        field_count=successful_count,
                        permission_field_count=created_count,
                    )
                    if deploy_credits > 0:
                        await get_entitlements().deduct(
                            user_id=str(user_id),
                            amount=deploy_credits,
                            reference_type="metadata_deploy",
                            reference_id=f"{batch_id}",
                        )
                        print(f"✅ [METADATA_DEPLOY_JOB] Deducted {deploy_credits} credits ({successful_count} fields, {created_count} permissions)")
            except Exception as e:
                print(f"⚠️ [METADATA_DEPLOY_JOB] Failed to deduct credits: {e}")

    async def _deploy_custom_object(
        self,
        batch_id: str,
        user_id: str,
        object_api_name: str,
        source_token: str,
        source_url: str,
        dest_token: str,
        dest_url: str,
        _refresh_tokens,
        _is_401,
    ) -> Dict[str, Any]:
        """
        Create a custom object on the destination org using the Metadata SOAP API
        (createMetadata). This works in all org types including Developer Edition,
        where the Metadata REST API retrieveRequest returns 404.

        Flow:
          1. Fetch object label/sharingModel from source via EntityDefinition SOQL (Tooling API).
          2. Call createMetadata SOAP on destination to create the object.

        Writes a migration record (index -1) of type 'CustomObject'.
        Returns {"success": True/False, "action": ..., "error": ...}.
        """
        print(f"   [DEPLOY_CUSTOM_OBJECT] Deploying {object_api_name} to destination ...")
        source_data = {
            "field_api_name": object_api_name,
            "label": object_api_name,
            "type": "CustomObject",
            "object_api_name": object_api_name,
        }
        record_id = await MigrationRecordsService.create_migration_record(
            batch_id, str(user_id), -1, source_data, {}
        )

        try:
            # Step 1: Fetch object metadata from source to get label, sharingModel etc.
            meta = await get_custom_object_metadata(source_token, source_url, object_api_name)
            if meta:
                label = meta.get("label") or object_api_name
                plural_label = meta.get("pluralLabel") or (label + "s")
                sharing_model = meta.get("sharingModel") or "ReadWrite"
                deployment_status = meta.get("deploymentStatus") or "Deployed"
                description = meta.get("description") or ""
            else:
                # Fall back to deriving from API name if source fetch fails
                from utils.salesforce_tooling_api import _parse_custom_object_developer_name
                parsed = _parse_custom_object_developer_name(object_api_name)
                label = parsed.get("developer_name") or object_api_name
                plural_label = label + "s"
                sharing_model = "ReadWrite"
                deployment_status = "Deployed"
                description = ""
                print(f"   ⚠️ [DEPLOY_CUSTOM_OBJECT] Could not fetch source metadata, using defaults for {object_api_name}")

            print(f"   [DEPLOY_CUSTOM_OBJECT] Creating {object_api_name} (label='{label}', sharingModel='{sharing_model}') on destination ...")

            # Step 2: Create the object on destination via Metadata SOAP API
            result = await create_custom_object_via_soap(
                access_token=dest_token,
                instance_url=dest_url,
                object_api_name=object_api_name,
                label=label,
                plural_label=plural_label,
                sharing_model=sharing_model,
                deployment_status=deployment_status,
                description=description,
            )

            if not result.get("success"):
                err = result.get("error") or "Custom object creation failed"
                if _is_401(str(err)):
                    refreshed = await _refresh_tokens()
                    if refreshed:
                        result = await create_custom_object_via_soap(
                            access_token=dest_token,
                            instance_url=dest_url,
                            object_api_name=object_api_name,
                            label=label,
                            plural_label=plural_label,
                            sharing_model=sharing_model,
                            deployment_status=deployment_status,
                            description=description,
                        )
                        err = result.get("error") or err
                if not result.get("success"):
                    print(f"   ❌ [DEPLOY_CUSTOM_OBJECT] Failed: {err}")
                    if record_id:
                        await MigrationRecordsService.update_migration_record_status(
                            record_id, status="failed", error_message=str(err)
                        )
                    return {"success": False, "error": str(err)}

            print(f"   ✅ [DEPLOY_CUSTOM_OBJECT] {object_api_name} created successfully on destination")
            view_link = _custom_object_view_link(dest_url, object_api_name)
            salesforce_results = {
                "action": "created",
                "success": True,
                "view_link": view_link,
            }
            if record_id:
                await MigrationRecordsService.update_migration_record_status(
                    record_id, status="success", salesforce_results=salesforce_results
                )
            return {"success": True, "action": "created", "view_link": view_link}

        except Exception as e:
            err_text = str(e)
            print(f"   ❌ [DEPLOY_CUSTOM_OBJECT] Exception: {err_text}")
            if record_id:
                await MigrationRecordsService.update_migration_record_status(
                    record_id, status="failed", error_message=err_text
                )
            return {"success": False, "error": err_text}

    async def _deploy_record_types(
        self,
        batch_id: str,
        user_id: str,
        object_api_name: str,
        source_token: str,
        source_url: str,
        dest_token: str,
        dest_url: str,
        dest_org_id: str,
        field_index_offset: int = 0,
    ) -> tuple:
        """
        Deploy all non-master record types from source to destination for the given object.
        Writes one migration_record per record type (same pattern as fields).
        Also upserts successfully deployed record types into salesforce_object_record_types
        for the destination org so future migrations can resolve names.

        Returns (successful_count, failed_count).
        """
        from db.connection import database

        successful_count = 0
        failed_count = 0

        record_types = await get_record_types_for_object(source_token, source_url, object_api_name)
        if not record_types:
            print(f"   [RT_DEPLOY] No non-master record types found for {object_api_name} on source")
            return 0, 0

        print(f"   [RT_DEPLOY] Deploying {len(record_types)} record type(s) for {object_api_name}")

        for idx, rt in enumerate(record_types):
            developer_name = rt.get("DeveloperName", "")
            name = rt.get("Name", developer_name)
            description = rt.get("Description", "")
            is_active = rt.get("IsActive", True)
            is_person_type = rt.get("IsPersonType", False)

            source_data = {
                # Use field_api_name + label so the frontend metadata table renders them
                # in the existing Field / Label columns without any frontend changes
                "field_api_name": developer_name,
                "label": name,
                "type": "RecordType",
                # Extra context kept for debugging / future use
                "record_type_developer_name": developer_name,
                "record_type_name": name,
                "object_api_name": object_api_name,
                "is_person_type": is_person_type,
            }
            record_id = None
            try:
                record_id = await MigrationRecordsService.create_migration_record(
                    batch_id, str(user_id), field_index_offset + idx, source_data, {}
                )
                result = await create_or_update_record_type(
                    dest_token, dest_url,
                    object_api_name=object_api_name,
                    developer_name=developer_name,
                    name=name,
                    description=description,
                    is_active=is_active,
                    is_person_type=is_person_type,
                )

                if result.get("success") or result.get("action") == "skipped":
                    action = result.get("action", "created")
                    rt_id = result.get("id")
                    view_link = _record_type_view_link(dest_url, object_api_name, rt_id) if rt_id else ""
                    salesforce_results = {
                        "id": rt_id,
                        "action": action,
                        "success": True,
                        "view_link": view_link,
                        "record_type_developer_name": developer_name,
                    }
                    await MigrationRecordsService.update_migration_record_status(
                        record_id, status="success", salesforce_results=salesforce_results
                    )
                    successful_count += 1
                    print(f"      [RT_DEPLOY] {action}: {object_api_name}.{developer_name}")

                    # Upsert into salesforce_object_record_types for destination org (Step 4)
                    if action in ("created", "updated"):
                        try:
                            # Fetch the destination salesforce_objects uuid_id
                            obj_row = await database.fetch_one(
                                """
                                SELECT uuid_id FROM salesforce_objects
                                WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
                                LIMIT 1
                                """,
                                values={
                                    "user_id": str(user_id),
                                    "org_id": dest_org_id,
                                    "object_api_name": object_api_name,
                                },
                            )
                            salesforce_object_uuid = obj_row["uuid_id"] if obj_row else None

                            # Resolve the actual Salesforce RecordTypeId from destination
                            # rt_id from Tooling API is the metadata Id, not the 18-char record type Id
                            # Query the REST API to get the real RecordTypeId
                            from urllib.parse import quote as url_quote
                            import httpx as _httpx
                            obj_esc = developer_name.replace("'", "\\'")
                            soql = (
                                f"SELECT Id FROM RecordType WHERE SobjectType = '{object_api_name}' "
                                f"AND DeveloperName = '{obj_esc}' LIMIT 1"
                            )
                            rt_query_url = f"{dest_url}/services/data/v59.0/query?q={url_quote(soql, safe='')}"
                            async with _httpx.AsyncClient(timeout=15) as client:
                                rt_resp = await client.get(
                                    rt_query_url,
                                    headers={"Authorization": f"Bearer {dest_token}"},
                                )
                            real_rt_id = None
                            if rt_resp.status_code == 200:
                                rt_records = (rt_resp.json() or {}).get("records") or []
                                if rt_records:
                                    real_rt_id = rt_records[0].get("Id")

                            if real_rt_id:
                                await database.execute(
                                    """
                                    INSERT INTO salesforce_object_record_types (
                                        salesforce_object_uuid, user_id, org_id, object_api_name,
                                        record_type_id, name, developer_name,
                                        is_active, is_default, is_master
                                    )
                                    VALUES (
                                        :salesforce_object_uuid, :user_id, :org_id, :object_api_name,
                                        :record_type_id, :name, :developer_name,
                                        :is_active, false, false
                                    )
                                    ON CONFLICT (user_id, org_id, object_api_name, record_type_id) DO UPDATE SET
                                        name = EXCLUDED.name,
                                        developer_name = EXCLUDED.developer_name,
                                        is_active = EXCLUDED.is_active,
                                        salesforce_object_uuid = EXCLUDED.salesforce_object_uuid
                                    """,
                                    values={
                                        "salesforce_object_uuid": salesforce_object_uuid,
                                        "user_id": str(user_id),
                                        "org_id": dest_org_id,
                                        "object_api_name": object_api_name,
                                        "record_type_id": real_rt_id,
                                        "name": name,
                                        "developer_name": developer_name,
                                        "is_active": is_active,
                                    },
                                )
                                # Mark object as having record types
                                await database.execute(
                                    """
                                    UPDATE salesforce_objects
                                    SET has_record_types = true
                                    WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
                                    """,
                                    values={
                                        "user_id": str(user_id),
                                        "org_id": dest_org_id,
                                        "object_api_name": object_api_name,
                                    },
                                )
                                print(f"      [RT_DEPLOY] Upserted record type {developer_name} (Id={real_rt_id}) into DB for dest org")
                        except Exception as db_err:
                            print(f"      [RT_DEPLOY] DB upsert failed for {developer_name}: {db_err}")
                else:
                    err_msg = (result.get("error") or "unknown")[:500]
                    action = result.get("action", "failed")
                    await MigrationRecordsService.update_migration_record_status(
                        record_id, status="failed",
                        salesforce_results={"success": False, "action": action, "error": err_msg, "view_link": ""},
                        error_message=err_msg,
                    )
                    failed_count += 1
                    print(f"      [RT_DEPLOY] Failed: {object_api_name}.{developer_name} — {err_msg}")

            except Exception as e:
                err_str = str(e)[:500]
                if not record_id:
                    source_data["_error"] = err_str
                    record_id = await MigrationRecordsService.create_migration_record(
                        batch_id, str(user_id), field_index_offset + idx, source_data, {}
                    )
                await MigrationRecordsService.update_migration_record_status(
                    record_id, status="failed",
                    salesforce_results={"success": False, "action": "failed", "error": err_str, "view_link": ""},
                    error_message=err_str,
                )
                failed_count += 1
                print(f"      [RT_DEPLOY] Exception for {object_api_name}.{developer_name}: {e}")

        return successful_count, failed_count
