import json
from collections import defaultdict
from typing import Dict, Any, List, Optional
from services.migration_records_service import MigrationRecordsService
from utils.salesforceapimigration import get_valid_salesforce_token, delete_salesforce_record
from utils.salesforce_metadata_api import (
    delete_custom_fields_via_metadata_api,
    delete_custom_object_via_metadata_api,
)
from services.google_sheets_service import GoogleSheetsService
from services.gsheets_migration_service import GoogleSheetsMigrationService
from services.database_migration_service import DatabaseMigrationService
from services.sql_connector_service import SQLConnectorService


class RollbackService:
    """Service for rolling back (deleting) migrated records"""
    
    @staticmethod
    async def rollback_batch(
        batch_id: str,
        user_id: str
    ) -> Dict[str, Any]:
        """
        Main rollback function that:
        1. Validates batch ownership
        2. Retrieves all migration records for the batch
        3. Filters for "created" records only
        4. Groups records by destination type
        5. Calls appropriate rollback handler
        6. Updates batch and record statuses
        
        Args:
            batch_id: The batch ID to rollback
            user_id: The user ID (for validation)
            
        Returns:
            Dict with rollback results
        """
        try:
            print(f"🔄 [ROLLBACK] Starting rollback for batch_id={batch_id}, user_id={user_id}")
            
            # Get batch info to validate ownership and get destination type
            batch_query = """
            SELECT id, user_id, destination_type, mapping_config, status, document_type, batch_type
            FROM migration_batches
            WHERE id = :batch_id
            """
            from db.connection import database
            batch_result = await database.fetch_one(
                batch_query, 
                values={"batch_id": batch_id}
            )
            
            if not batch_result:
                print(f"❌ [ROLLBACK] Batch not found: batch_id={batch_id}")
                return {
                    "status": "failed",
                    "message": "Batch not found",
                    "total_records": 0,
                    "deleted_records": 0,
                    "failed_records": 0,
                    "errors": []
                }
            
            batch = dict(batch_result)
            print(f"✅ [ROLLBACK] Batch found: batch_id={batch_id}, destination_type={batch.get('destination_type')}, status={batch.get('status')}")
            
            # Validate ownership - convert both to strings for comparison (UUID vs string mismatch)
            batch_user_id = str(batch["user_id"]) if batch["user_id"] else None
            request_user_id = str(user_id) if user_id else None
            
            if batch_user_id != request_user_id:
                print(f"❌ [ROLLBACK] Unauthorized: batch user_id={batch_user_id} (type: {type(batch['user_id'])}), request user_id={request_user_id} (type: {type(user_id)})")
                return {
                    "status": "failed",
                    "message": "Unauthorized: You don't own this batch",
                    "total_records": 0,
                    "deleted_records": 0,
                    "failed_records": 0,
                    "errors": []
                }
            
            # Check batch status
            if batch["status"] == "in_progress":
                print(f"❌ [ROLLBACK] Cannot rollback batch in progress: batch_id={batch_id}")
                return {
                    "status": "failed",
                    "message": "Cannot rollback a batch that is still in progress",
                    "total_records": 0,
                    "deleted_records": 0,
                    "failed_records": 0,
                    "errors": []
                }
            
            # Get migration records - use iteration for data batches to avoid loading all at once
            batch_type = batch.get("batch_type") or "data"
            destination_type = batch.get("destination_type", "salesforce")
            created_records = []
            skipped_count = 0

            def _is_created_record(record: Dict[str, Any], bt: str) -> bool:
                """True if record has created status and should be rolled back."""
                if record.get("status") != "success":
                    return False
                salesforce_results = record.get("salesforce_results")
                if not salesforce_results:
                    return False
                if isinstance(salesforce_results, str):
                    try:
                        salesforce_results = json.loads(salesforce_results)
                    except Exception:
                        return False
                if bt == "metadata":
                    return isinstance(salesforce_results, dict) and salesforce_results.get("action") == "created"
                if isinstance(salesforce_results, dict):
                    for _dest, result in salesforce_results.items():
                        if isinstance(result, dict) and result.get("status") == "created":
                            return True
                return False

            if batch_type == "metadata":
                # Metadata batches are typically small; load all (needs full set for field/object ordering)
                print(f"📋 [ROLLBACK] Fetching metadata migration records for batch_id={batch_id}")
                records = await MigrationRecordsService.get_migration_records(batch_id)
                for record in records:
                    if _is_created_record(record, batch_type):
                        created_records.append(record)
                    else:
                        skipped_count += 1
            else:
                # Data batches: stream via iterate_migration_records (avoids single large query)
                print(f"📋 [ROLLBACK] Streaming migration records for batch_id={batch_id}")
                async for rec_batch in MigrationRecordsService.iterate_migration_records(
                    batch_id, batch_size=500
                ):
                    for record in rec_batch:
                        if _is_created_record(record, batch_type):
                            created_records.append(record)
                        else:
                            skipped_count += 1

            print(f"✅ [ROLLBACK] Filtered records: {len(created_records)} created, {skipped_count} skipped (batch_type={batch_type})")
            
            if not created_records:
                print(f"ℹ️ [ROLLBACK] No created records to rollback for batch_id={batch_id}")
                return {
                    "status": "success",
                    "message": "No created records found to rollback",
                    "total_records": 0,
                    "deleted_records": 0,
                    "failed_records": 0,
                    "errors": []
                }

            # Rollback is included; no credit check or deduction

            # Parse mapping_config
            mapping_config = batch.get("mapping_config")
            if isinstance(mapping_config, str):
                try:
                    mapping_config = json.loads(mapping_config)
                except Exception as e:
                    print(f"⚠️ [ROLLBACK] Failed to parse mapping_config as JSON: {e}")
                    mapping_config = {}
            elif mapping_config is None:
                mapping_config = {}
            
            destination_type = batch.get("destination_type", "salesforce")
            print(f"🎯 [ROLLBACK] Routing to handler (batch_type={batch_type}, destination_type={destination_type}) with {len(created_records)} records")
            
            # Route to appropriate handler
            if batch_type == "metadata":
                result = await RollbackService._rollback_metadata_deploy(
                    records=created_records,
                    user_id=user_id,
                    mapping_config=mapping_config
                )
            elif destination_type == "salesforce":
                result = await RollbackService._rollback_salesforce_records(
                    records=created_records,
                    user_id=user_id,
                    mapping_config=mapping_config
                )
            elif destination_type == "google_sheets":
                result = await RollbackService._rollback_google_sheets_records(
                    records=created_records,
                    user_id=user_id,
                    mapping_config=mapping_config
                )
            elif destination_type == "sql":
                result = await RollbackService._rollback_sql_records(
                    records=created_records,
                    user_id=user_id,
                    mapping_config=mapping_config
                )
            else:
                print(f"❌ [ROLLBACK] Unsupported destination type: {destination_type}")
                return {
                    "status": "failed",
                    "message": f"Unsupported destination type: {destination_type}",
                    "total_records": len(created_records),
                    "deleted_records": 0,
                    "failed_records": 0,
                    "errors": []
                }
            
            print(f"📈 [ROLLBACK] Rollback result: status={result.get('status')}, deleted={result.get('deleted_records')}, failed={result.get('failed_records')}")
            
            # Generate user-friendly rollback message
            rollback_message = None
            if result.get("deleted_records", 0) > 0:
                # Extract source and destination info
                source_name = (
                    mapping_config.get("source_name")
                    or mapping_config.get("document_type")
                    or batch.get("document_type")
                    or "Unknown Source"
                )

                # Get destination name from mapping_config
                destination_name = mapping_config.get("destination_name")
                if not destination_name:
                    # Fallback to destination_type if no destination_name
                    destination_type = batch.get("destination_type", "Unknown")
                    destination_name = destination_type.replace("_", " ").title()

                # Create readable destination type label
                batch_destination_type = batch.get("destination_type", "")
                destination_type_label = {
                    "salesforce": "Salesforce",
                    "google_sheets": "Google Sheets",
                    "sql": "SQL Database",
                }.get(batch_destination_type, batch_destination_type.replace("_", " ").title())

                # Attach additional context for frontend/chat consumers
                # Prefer not to overwrite if handler already set these keys.
                result.setdefault("source_name", source_name)
                # If destination is Salesforce (including metadata deploys), treat source as Salesforce as well.
                inferred_source_type = mapping_config.get("source_type") or (
                    "salesforce" if batch_destination_type == "salesforce" else None
                )
                if inferred_source_type:
                    result.setdefault("source_type", inferred_source_type)
                result.setdefault("destination_name", destination_name)
                result.setdefault("destination_type", batch_destination_type or destination_type_label)

                # Generate user-friendly message (no batch_id)
                attachment_text = ""
                if result.get("deleted_attachments", 0) > 0:
                    attachment_text = f" and {result['deleted_attachments']} attachment(s)"

                rollback_message = (
                    f"{result['deleted_records']} record(s){attachment_text} rolled back from "
                    f"{destination_name} ({destination_type_label}). "
                    f"Source: {source_name}"
                )

                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="rolled_back",
                    rollback_summary=rollback_message,
                )
            else:
                print(f"⚠️ [ROLLBACK] No deleted records, skipping rollback message generation")

            # Rollback is free; no credit deduction

            print(f"✅ [ROLLBACK] Rollback completed for batch_id={batch_id}")
            
            # Return result with rollback message
            result["rollback_message"] = rollback_message
            return result
            
        except Exception as e:
            print(f"❌ Error in rollback_batch: {e}")
            import traceback
            traceback.print_exc()
            return {
                "status": "failed",
                "message": f"Rollback failed: {str(e)}",
                "total_records": 0,
                "deleted_records": 0,
                "failed_records": 0,
                "errors": [str(e)]
            }
    
    @staticmethod
    async def _rollback_metadata_deploy(
        records: List[Dict[str, Any]],
        user_id: str,
        mapping_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Rollback metadata deploy: delete CustomField and RecordType records that were created.
        - CustomFields: deleted via Metadata API destructive deploy (Tooling API DELETE not supported).
        - RecordTypes: deleted via Tooling API DELETE using the Salesforce Id stored in salesforce_results.
        Updates migration_record status to rolled_back on success.
        """
        deleted_count = 0
        failed_count = 0
        errors = []

        dest_org_id = mapping_config.get("dest_org_id")
        dest_connected_app_id = mapping_config.get("dest_connected_app_id")
        print(f"🔵 [ROLLBACK-MD] Starting metadata deploy rollback for {len(records)} record(s)")
        print(f"   [ROLLBACK-MD] mapping_config request: dest_org_id={dest_org_id}, dest_connected_app_id={dest_connected_app_id} (type={type(dest_connected_app_id).__name__})")

        try:
            token_info = await get_valid_salesforce_token(
                user_id=user_id,
                org_id=dest_org_id,
                connected_app_id=dest_connected_app_id
            )
        except Exception as e:
            msg = f"Failed to get Salesforce token: {str(e)}"
            print(f"❌ [ROLLBACK-MD] Token fetch failed: {e}")
            return {
                "status": "failed",
                "message": msg,
                "deleted_records": 0,
                "failed_records": len(records),
                "errors": [msg]
            }

        if not token_info:
            msg = "Salesforce token not found for destination org"
            print(f"❌ [ROLLBACK-MD] No token returned for dest_org_id={dest_org_id}")
            return {
                "status": "failed",
                "message": msg,
                "deleted_records": 0,
                "failed_records": len(records),
                "errors": [msg]
            }

        access_token = token_info["access_token"]
        instance_url = token_info["instance_url"]
        token_org_id = token_info.get("org_id")
        token_connected_app_id = token_info.get("connected_app_id")
        token_row_id = token_info.get("id") or token_info.get("token_id")
        print(f"✅ [ROLLBACK-MD] Token obtained: instance_url={instance_url}")
        print(f"   [ROLLBACK-MD] Token identity: org_id={token_org_id}, connected_app_id={token_connected_app_id}, salesforce_tokens.id={token_row_id}")
        if (dest_org_id and token_org_id and dest_org_id != token_org_id) or (dest_connected_app_id is not None and token_connected_app_id != dest_connected_app_id):
            print(f"   ⚠️ [ROLLBACK-MD] Token mismatch: requested dest_org_id={dest_org_id} connected_app_id={dest_connected_app_id} vs token org_id={token_org_id} connected_app_id={token_connected_app_id}")

        object_api_name = mapping_config.get("object_api_name") or ""

        # Split records by type: CustomFields, RecordTypes (skipped), CustomObjects.
        # Order matters: fields must be deleted before the object itself.
        fields_to_delete = []       # list of (full_name, migration_record_id)
        skipped_record_types = []   # list of {"name": str, "developer_name": str, "view_link": str}
        custom_object_to_delete = None  # (object_api_name, migration_record_id) — at most one

        for idx, record in enumerate(records):
            source_data = record.get("source_data") or {}
            if isinstance(source_data, str):
                try:
                    source_data = json.loads(source_data) if source_data else {}
                except Exception:
                    source_data = {}

            salesforce_results = record.get("salesforce_results") or {}
            if isinstance(salesforce_results, str):
                try:
                    salesforce_results = json.loads(salesforce_results) if salesforce_results else {}
                except Exception:
                    salesforce_results = {}

            metadata_type = source_data.get("type", "")
            obj_name = source_data.get("object_api_name") or object_api_name

            if metadata_type == "RecordType":
                developer_name = source_data.get("record_type_developer_name") or source_data.get("field_api_name", "")
                rt_label = source_data.get("record_type_name") or source_data.get("label") or developer_name
                view_link = salesforce_results.get("view_link") or ""
                skipped_record_types.append({
                    "name": rt_label,
                    "developer_name": developer_name,
                    "view_link": view_link,
                })
                print(f"   ⏭️ [ROLLBACK-MD] Skipping RecordType {developer_name} — Salesforce does not allow API deletion")
            elif metadata_type == "CustomObject":
                # Store for deletion after all fields are removed
                custom_object_to_delete = (obj_name, record.get("id"))
                print(f"   🗑️ [ROLLBACK-MD] CustomObject {obj_name} queued for deletion (after fields)")
            else:
                field_name = source_data.get("field_api_name")
                if not obj_name or not field_name:
                    failed_count += 1
                    errors.append("Missing object_api_name or field_api_name in migration record")
                    print(f"   ⚠️ [ROLLBACK-MD] Record {idx + 1}: missing object_api_name or field_api_name")
                    continue
                fields_to_delete.append((f"{obj_name}.{field_name}", record.get("id")))

        def _is_401_md(text: str) -> bool:
            return any(k in text for k in ("401", "Unauthorized", "INVALID_SESSION_ID", "Session expired"))

        # --- Delete CustomFields via Metadata API destructive deploy ---
        if fields_to_delete:
            full_names = [t[0] for t in fields_to_delete]
            migration_record_ids = [t[1] for t in fields_to_delete]
            print(f"   🗑️ [ROLLBACK-MD] Deleting {len(full_names)} CustomField(s) via Metadata API: {full_names}")
            md_token_refreshed = False
            for md_attempt in range(2):
                try:
                    result = await delete_custom_fields_via_metadata_api(access_token, instance_url, full_names)
                    if result.get("success") and result.get("numberFailed", 0) == 0:
                        deleted_count += result.get("numberDeployed") or len(full_names)
                        for mig_id in migration_record_ids:
                            if mig_id:
                                await MigrationRecordsService.update_migration_record_status(
                                    record_id=mig_id,
                                    status="rolled_back"
                                )
                        print(f"   ✅ [ROLLBACK-MD] Metadata API delete succeeded: {len(full_names)} field(s) deleted")
                    else:
                        err = result.get("error") or "Delete failed"
                        # Retry on 401
                        if _is_401_md(str(err)) and md_attempt == 0 and not md_token_refreshed:
                            try:
                                ti = await get_valid_salesforce_token(
                                    user_id=user_id, org_id=dest_org_id,
                                    connected_app_id=dest_connected_app_id, force_refresh=True,
                                )
                                access_token = ti["access_token"]
                                md_token_refreshed = True
                                print(f"   ⚠️ [ROLLBACK-MD] 401 on field delete — token refreshed, retrying")
                                continue
                            except Exception:
                                pass
                        failed_count += result.get("numberFailed") or len(full_names)
                        errors.append(err)
                        print(f"   ❌ [ROLLBACK-MD] Metadata API delete failed: {err[:500] if isinstance(err, str) else err}")
                    break
                except Exception as e:
                    err_str = str(e)
                    if _is_401_md(err_str) and md_attempt == 0 and not md_token_refreshed:
                        try:
                            ti = await get_valid_salesforce_token(
                                user_id=user_id, org_id=dest_org_id,
                                connected_app_id=dest_connected_app_id, force_refresh=True,
                            )
                            access_token = ti["access_token"]
                            md_token_refreshed = True
                            print(f"   ⚠️ [ROLLBACK-MD] 401 exception on field delete — token refreshed, retrying")
                            continue
                        except Exception:
                            pass
                    failed_count += len(full_names)
                    errors.append(err_str)
                    print(f"   ❌ [ROLLBACK-MD] Exception deleting fields: {type(e).__name__}: {err_str}")
                    break

        # --- Delete CustomObject via Metadata API destructive deploy (after fields) ---
        if custom_object_to_delete:
            co_api_name, co_record_id = custom_object_to_delete
            print(f"   🗑️ [ROLLBACK-MD] Deleting CustomObject {co_api_name} via Metadata API ...")
            co_token_refreshed = False
            for co_attempt in range(2):
                try:
                    co_result = await delete_custom_object_via_metadata_api(access_token, instance_url, co_api_name)
                    if co_result.get("success"):
                        deleted_count += 1
                        if co_record_id:
                            await MigrationRecordsService.update_migration_record_status(
                                record_id=co_record_id,
                                status="rolled_back"
                            )
                        print(f"   ✅ [ROLLBACK-MD] CustomObject {co_api_name} deleted successfully")
                    else:
                        err = co_result.get("error") or "CustomObject delete failed"
                        if _is_401_md(str(err)) and co_attempt == 0 and not co_token_refreshed:
                            try:
                                ti = await get_valid_salesforce_token(
                                    user_id=user_id, org_id=dest_org_id,
                                    connected_app_id=dest_connected_app_id, force_refresh=True,
                                )
                                access_token = ti["access_token"]
                                co_token_refreshed = True
                                print(f"   ⚠️ [ROLLBACK-MD] 401 on object delete — token refreshed, retrying")
                                continue
                            except Exception:
                                pass
                        failed_count += 1
                        errors.append(f"CustomObject {co_api_name}: {err}")
                        print(f"   ❌ [ROLLBACK-MD] CustomObject delete failed: {err}")
                    break
                except Exception as e:
                    err_str = str(e)
                    if _is_401_md(err_str) and co_attempt == 0 and not co_token_refreshed:
                        try:
                            ti = await get_valid_salesforce_token(
                                user_id=user_id, org_id=dest_org_id,
                                connected_app_id=dest_connected_app_id, force_refresh=True,
                            )
                            access_token = ti["access_token"]
                            co_token_refreshed = True
                            print(f"   ⚠️ [ROLLBACK-MD] 401 exception on object delete — token refreshed, retrying")
                            continue
                        except Exception:
                            pass
                    failed_count += 1
                    errors.append(f"CustomObject {co_api_name}: {err_str}")
                    print(f"   ❌ [ROLLBACK-MD] Exception deleting CustomObject: {err_str}")
                    break

        print(f"📊 [ROLLBACK-MD] Done: deleted={deleted_count}, failed={failed_count}, skipped_record_types={len(skipped_record_types)}, errors={errors}")
        result = {
            "status": "success" if failed_count == 0 else "partial_success",
            "deleted_records": deleted_count,
            "failed_records": failed_count,
            "errors": errors,
        }
        if skipped_record_types:
            rt_names = [rt["name"] for rt in skipped_record_types]
            result["record_types_skipped"] = skipped_record_types
            result["record_type_note"] = (
                f"Salesforce does not allow deleting Record Types via any API. "
                f"The following Record Type(s) must be manually deleted from "
                f"Salesforce Setup > Object Manager > [Object] > Record Types: "
                f"{', '.join(rt_names)}"
            )
        return result
    
    @staticmethod
    async def _rollback_salesforce_records(
        records: List[Dict[str, Any]],
        user_id: str,
        mapping_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Rollback Salesforce records (only "created" records):
        1. Filter records where salesforce_results.status = "created"
        2. Extract Salesforce IDs from salesforce_results.id
        3. Group by object type
        4. Delete records using Salesforce REST API DELETE endpoint
        5. Handle errors gracefully (some records may already be deleted)
        """
        # Count unique Salesforce record Ids, not migration rows
        deleted_ids = set()
        failed_ids = set()
        errors = []
        sf_token_refreshed = False  # Only force-refresh once per rollback job

        def _is_401_sf(text: str) -> bool:
            return any(k in text for k in ("401", "Unauthorized", "INVALID_SESSION_ID", "Session expired"))

        print(f"🔵 [ROLLBACK-SF] Starting Salesforce rollback for {len(records)} records")
        
        # Extract destination org info from mapping_config
        dest_org_id = mapping_config.get("org_id")
        dest_connected_app_id = mapping_config.get("connected_app_id")
        dest_name = mapping_config.get("destination_name")

        # Get Salesforce token for destination org
        try:
            if dest_org_id or dest_connected_app_id:
                print(f"🔑 [ROLLBACK-SF] Fetching destination Salesforce token (org_id={dest_org_id}, connected_app_id={dest_connected_app_id})")
                token_info = await get_valid_salesforce_token(
                    user_id=user_id,
                    org_id=dest_org_id,
                    connected_app_id=dest_connected_app_id
                )
            elif dest_name and str(dest_name).strip().lower() not in ("", "unknown"):
                from services.migration.salesforce_runner import _resolve_destination_token

                print(f"🔑 [ROLLBACK-SF] Resolving token by destination_name={dest_name!r}")
                token_record = await _resolve_destination_token(user_id, str(dest_name).strip())
                token_info = await get_valid_salesforce_token(
                    user_id=user_id,
                    org_id=token_record.get("org_id"),
                    connected_app_id=token_record.get("connected_app_id"),
                )
            else:
                print(f"⚠️ [ROLLBACK-SF] No destination org info in mapping_config, using default token")
                token_info = await get_valid_salesforce_token(user_id)
            
            if not token_info:
                error_msg = f"Salesforce token not found for user_id={user_id}"
                if dest_org_id or dest_connected_app_id:
                    error_msg += f" (org_id={dest_org_id}, connected_app_id={dest_connected_app_id})"
                print(f"❌ [ROLLBACK-SF] {error_msg}")
                return {
                    "status": "failed",
                    "message": error_msg,
                    "deleted_records": 0,
                    "failed_records": len(records),
                    "errors": [error_msg]
                }
            
            access_token = token_info["access_token"]
            instance_url = token_info["instance_url"]
            token_org_id = token_info.get("org_id")
            token_connected_app_id = token_info.get("connected_app_id")
            
            print(f"✅ [ROLLBACK-SF] Salesforce token obtained, instance_url={instance_url}")
            
            # Verify we got the correct destination org token
            if dest_org_id and token_org_id and dest_org_id != token_org_id:
                print(f"⚠️ [ROLLBACK-SF] WARNING: Token org_id ({token_org_id}) doesn't match destination org_id ({dest_org_id})")
            if dest_connected_app_id and token_connected_app_id and dest_connected_app_id != token_connected_app_id:
                print(f"⚠️ [ROLLBACK-SF] WARNING: Token connected_app_id ({token_connected_app_id}) doesn't match destination connected_app_id ({dest_connected_app_id})")
                
        except Exception as e:
            print(f"❌ [ROLLBACK-SF] Failed to get Salesforce token: {str(e)}")
            return {
                "status": "failed",
                "message": f"Failed to get Salesforce token: {str(e)}",
                "deleted_records": 0,
                "failed_records": len(records),
                "errors": [str(e)]
            }
        
        # Group records by object type
        # Use mapping: object -> {record_id: [migration_record_ids]} to de-duplicate SF Ids
        records_by_object: Dict[str, Dict[str, List[Any]]] = {}
        
        for record in records:
            salesforce_results = record.get("salesforce_results")
            if isinstance(salesforce_results, str):
                try:
                    salesforce_results = json.loads(salesforce_results)
                except:
                    continue
            
            if not isinstance(salesforce_results, dict):
                continue
            
            for object_name, result in salesforce_results.items():
                if isinstance(result, dict) and result.get("status") == "created":
                    record_id = result.get("id")
                    if record_id:
                        if object_name not in records_by_object:
                            records_by_object[object_name] = {}
                        if record_id not in records_by_object[object_name]:
                            records_by_object[object_name][record_id] = []
                        records_by_object[object_name][record_id].append(record.get("id"))
                    else:
                        # Log missing IDs for debugging
                        migration_record_id = record.get("id", "unknown")
                        print(f"   ⚠️ [ROLLBACK-SF] Skipping {object_name} record - no ID found in salesforce_results. Migration record ID: {migration_record_id}, Result: {result}")
        
        print(f"📦 [ROLLBACK-SF] Grouped records by object: {list(records_by_object.keys())}")
        for obj_name, obj_records in records_by_object.items():
            total_refs = sum(len(v) for v in obj_records.values())
            print(f"   - {obj_name}: {total_refs} records (unique Ids: {len(obj_records)})")
        
        # Delete records by object type
        # Use Bulk API 2.0 for 50+ records, REST API for smaller batches
        BULK_API_THRESHOLD = 50
        
        for object_name, record_id_map in records_by_object.items():
            record_ids = list(record_id_map.keys())
            record_count = sum(len(v) for v in record_id_map.values())
            print(f"🗑️ [ROLLBACK-SF] Deleting {record_count} records from {object_name}")
            
            if record_count >= BULK_API_THRESHOLD:
                # Use Bulk API 2.0 for large batches
                print(f"   📦 [ROLLBACK-SF] Using Bulk API 2.0 for {record_count} records (threshold: {BULK_API_THRESHOLD}+)")
                try:
                    from services.salesforce_bulk_migration_service import bulk_delete_records
                    
                    # Log record IDs being deleted for debugging
                    print(f"   🔍 [ROLLBACK-SF] Attempting to delete {len(record_ids)} {object_name} records")
                    if record_ids:
                        print(f"   🔍 [ROLLBACK-SF] Sample record IDs (first 3): {record_ids[:3]}")
                        # Check for invalid/empty IDs
                        invalid_ids = [rid for rid in record_ids if not rid or len(rid) < 15]
                        if invalid_ids:
                            print(f"   ⚠️ [ROLLBACK-SF] Found {len(invalid_ids)} potentially invalid IDs (too short or empty)")
                            print(f"   ⚠️ [ROLLBACK-SF] Invalid IDs sample: {invalid_ids[:3]}")
                    
                    # Perform bulk delete — retry once with refreshed token on 401
                    bulk_result = None
                    for bulk_rb_attempt in range(2):
                        try:
                            bulk_result = await bulk_delete_records(
                                object_name=object_name,
                                record_ids=record_ids,
                                access_token=access_token,
                                instance_url=instance_url
                            )
                            break
                        except Exception as bulk_rb_exc:
                            if _is_401_sf(str(bulk_rb_exc)) and bulk_rb_attempt == 0 and not sf_token_refreshed:
                                try:
                                    ti = await get_valid_salesforce_token(
                                        user_id=user_id, org_id=dest_org_id,
                                        connected_app_id=dest_connected_app_id, force_refresh=True,
                                    )
                                    access_token = ti["access_token"]
                                    sf_token_refreshed = True
                                    print(f"   ⚠️ [ROLLBACK-SF] 401 on bulk delete — token refreshed, retrying")
                                    continue
                                except Exception:
                                    pass
                            raise
                    if bulk_result is None:
                        raise Exception("Bulk delete returned no result")
                    
                    # Log detailed results for debugging
                    print(f"   📊 [ROLLBACK-SF] Bulk delete result summary:")
                    print(f"      - Successful count: {bulk_result.get('successful_count', 0)}")
                    print(f"      - Failed count: {bulk_result.get('failed_count', 0)}")
                    print(f"      - Job status: {bulk_result.get('status', 'unknown')}")
                    
                    # Log failed results summary
                    if bulk_result.get("failed_results"):
                        print(f"   ⚠️ [ROLLBACK-SF] Failed results sample (first 3):")
                        for idx, result in enumerate(bulk_result["failed_results"][:3]):
                            error_msg = result.get("sf__Error") or result.get("Error", "Unknown error")
                            record_id = result.get("sf__Id") or result.get("Id", "Unknown")
                            print(f"      Failed result {idx+1}: Record {record_id} - {error_msg}")
                        if len(bulk_result["failed_results"]) > 3:
                            print(f"      ... and {len(bulk_result['failed_results']) - 3} more")
                        
                        # Analyze error patterns
                        error_patterns = {}
                        for result in bulk_result["failed_results"]:
                            error_msg = result.get("sf__Error") or result.get("Error", "Unknown error")
                            error_patterns[error_msg] = error_patterns.get(error_msg, 0) + 1
                        
                        if error_patterns:
                            print(f"   📊 [ROLLBACK-SF] Error summary:")
                            for error_msg, count in error_patterns.items():
                                print(f"      - {error_msg}: {count} records")
                    
                    # Process successful deletions
                    successful_ids = set()
                    if bulk_result.get("successful_results"):
                        print(f"   📋 [ROLLBACK-SF] Processing {len(bulk_result['successful_results'])} successful results")
                        for result in bulk_result["successful_results"]:
                            # Bulk API 2.0 returns sf__Id or Id
                            record_id = result.get("sf__Id") or result.get("Id") or result.get("id")
                            if record_id:
                                successful_ids.add(record_id)
                            else:
                                # Log if we can't extract ID
                                print(f"      ⚠️ [ROLLBACK-SF] Could not extract ID from successful result: {result}")
                    
                    # Process failed deletions
                    failed_ids_with_errors = {}
                    if bulk_result.get("failed_results"):
                        print(f"   📋 [ROLLBACK-SF] Processing {len(bulk_result['failed_results'])} failed results")
                        for result in bulk_result["failed_results"]:
                            # Bulk API 2.0 returns sf__Id or Id
                            record_id = result.get("sf__Id") or result.get("Id") or result.get("id")
                            # Bulk API 2.0 error field names can vary - check common variations
                            error_msg = (result.get("sf__Error") or 
                                       result.get("Error") or 
                                       result.get("error") or 
                                       result.get("sf__Message") or
                                       result.get("Message") or
                                       result.get("message") or
                                       "Unknown error")
                            # Treat "ENTITY_IS_DELETED" as logical success: record is already gone
                            if record_id and "entity_is_deleted" in error_msg.replace(" ", "").lower():
                                successful_ids.add(record_id)
                                continue
                            if record_id:
                                failed_ids_with_errors[record_id] = error_msg
                            else:
                                # Log if we can't extract ID - this is likely the issue
                                print(f"      ❌ [ROLLBACK-SF] Could not extract ID from failed result: {result}")
                                print(f"      ❌ [ROLLBACK-SF] Available keys in failed result: {list(result.keys())}")
                                # Try to add with a placeholder ID if we have row number
                                row_num = result.get("sf__RowNum") or result.get("RowNum") or result.get("row_num")
                                if row_num:
                                    failed_ids_with_errors[f"ROW_{row_num}"] = error_msg
                    
                    # Update migration records based on results (but count unique SF Ids)
                    for record_id, migration_record_ids in record_id_map.items():
                        if record_id in successful_ids:
                            for mig_id in migration_record_ids:
                                await MigrationRecordsService.update_migration_record_status(
                                    record_id=mig_id,
                                    status="rolled_back"
                                )
                            deleted_ids.add(record_id)
                        else:
                            error_msg = failed_ids_with_errors.get(record_id, "Unknown error during bulk delete")
                            for mig_id in migration_record_ids:
                                errors.append(f"Failed to delete {object_name} record {record_id}: {error_msg}")
                            failed_ids.add(record_id)
                    
                    print(f"   ✅ [ROLLBACK-SF] Bulk delete completed: {len(successful_ids)} deleted, {len(failed_ids_with_errors)} failed")
                    
                except Exception as e:
                    error_msg = f"Bulk delete failed for {object_name}: {str(e)}"
                    errors.append(error_msg)
                    failed_ids.update(record_ids)
                    print(f"   ❌ [ROLLBACK-SF] {error_msg}")
            else:
                # Use REST API for smaller batches
                print(f"   🔄 [ROLLBACK-SF] Using REST API for {record_count} records (below threshold: {BULK_API_THRESHOLD})")
                for record_id, migration_record_ids in record_id_map.items():
                    for rest_rb_attempt in range(2):
                        try:
                            print(f"   🔄 [ROLLBACK-SF] Deleting {object_name} record_id={record_id}")
                            await delete_salesforce_record(
                                access_token=access_token,
                                instance_url=instance_url,
                                object_name=object_name,
                                record_id=record_id
                            )
                            for mig_id in migration_record_ids:
                                print(f"   ✅ [ROLLBACK-SF] Successfully deleted {object_name} record_id={record_id}")
                                await MigrationRecordsService.update_migration_record_status(
                                    record_id=mig_id,
                                    status="rolled_back"
                                )
                            deleted_ids.add(record_id)
                            break
                        except Exception as e:
                            err_text = str(e)
                            if _is_401_sf(err_text) and rest_rb_attempt == 0 and not sf_token_refreshed:
                                try:
                                    ti = await get_valid_salesforce_token(
                                        user_id=user_id, org_id=dest_org_id,
                                        connected_app_id=dest_connected_app_id, force_refresh=True,
                                    )
                                    access_token = ti["access_token"]
                                    sf_token_refreshed = True
                                    print(f"   ⚠️ [ROLLBACK-SF] 401 on REST delete — token refreshed, retrying")
                                    continue
                                except Exception:
                                    pass
                            error_msg = f"Failed to delete {object_name} record {record_id}: {err_text}"
                            for mig_id in migration_record_ids:
                                errors.append(error_msg)
                            failed_ids.add(record_id)
                            print(f"   ❌ [ROLLBACK-SF] {error_msg}")
                            break
        
        deleted_count = len(deleted_ids)
        failed_count = len(failed_ids)
        total_unique = deleted_count + failed_count
        print(f"📊 [ROLLBACK-SF] Rollback summary: deleted={deleted_count}, failed={failed_count}")
        
        # Step 2: Delete attachments (ContentDocument records)
        # Delete attachments that were created or linked during migration
        # Also delete rich text images that were uploaded during migration
        print(f"📎 [ROLLBACK-SF] Collecting attachments and rich text images to delete...")
        content_document_ids = set()
        
        for record in records:
            salesforce_results = record.get("salesforce_results")
            if isinstance(salesforce_results, str):
                try:
                    salesforce_results = json.loads(salesforce_results)
                except:
                    continue
            
            if not isinstance(salesforce_results, dict):
                continue
            
            for object_name, result in salesforce_results.items():
                if isinstance(result, dict):
                    # Collect file attachments
                    attachments = result.get("attachments", [])
                    for attachment in attachments:
                        # Delete attachments that were created or linked during migration
                        # Statuses: "linked" (created and linked), "linked_existing" (linked existing file),
                        # "uploaded_not_linked" (created but link failed)
                        # Do NOT delete: "skipped_duplicate" (we didn't create/link it), "failed" (never created)
                        attachment_status = attachment.get("status")
                        if attachment_status in ["linked", "linked_existing", "uploaded_not_linked"]:
                            content_doc_id = attachment.get("content_document_id")
                            if content_doc_id:
                                content_document_ids.add(content_doc_id)
                    
                    # Collect rich text images
                    rich_text_images = result.get("rich_text_images", [])
                    for rich_text_image in rich_text_images:
                        # Rich text images are always uploaded (status: "uploaded")
                        content_doc_id = rich_text_image.get("content_document_id")
                        if content_doc_id:
                            content_document_ids.add(content_doc_id)
        
        deleted_attachments = 0
        failed_attachments = 0
        
        if content_document_ids:
            print(f"🗑️ [ROLLBACK-SF] Deleting {len(content_document_ids)} attachment files...")
            
            # Use Bulk API 2.0 for 50+ attachments, REST API for smaller batches
            if len(content_document_ids) >= BULK_API_THRESHOLD:
                print(f"   📦 [ROLLBACK-SF] Using Bulk API 2.0 for {len(content_document_ids)} attachments (threshold: {BULK_API_THRESHOLD}+)")
                try:
                    from services.salesforce_bulk_migration_service import bulk_delete_records
                    
                    bulk_result = await bulk_delete_records(
                        object_name="ContentDocument",
                        record_ids=list(content_document_ids),
                        access_token=access_token,
                        instance_url=instance_url
                    )
                    
                    deleted_attachments = bulk_result.get("successful_count", 0)
                    failed_attachments = bulk_result.get("failed_count", 0)
                    
                    print(f"   📊 [ROLLBACK-SF] Bulk attachment deletion: {deleted_attachments} deleted, {failed_attachments} failed")
                except Exception as e:
                    print(f"   ❌ [ROLLBACK-SF] Bulk attachment deletion failed: {e}")
                    import traceback
                    traceback.print_exc()
                    # Fall back to individual deletions
                    failed_attachments = len(content_document_ids)
            else:
                # Use REST API for smaller batches
                print(f"   📝 [ROLLBACK-SF] Using REST API for {len(content_document_ids)} attachments")
                for content_doc_id in content_document_ids:
                    try:
                        result = await delete_salesforce_record(
                            access_token=access_token,
                            instance_url=instance_url,
                            object_name="ContentDocument",
                            record_id=content_doc_id
                        )
                        if result.get("success"):
                            deleted_attachments += 1
                        else:
                            failed_attachments += 1
                            errors.append(f"Failed to delete attachment {content_doc_id}: {result.get('message', 'Unknown error')}")
                    except Exception as e:
                        print(f"   ⚠️ [ROLLBACK-SF] Failed to delete attachment {content_doc_id}: {e}")
                        failed_attachments += 1
                        errors.append(f"Failed to delete attachment {content_doc_id}: {str(e)}")
            
            if deleted_attachments > 0 or failed_attachments > 0:
                print(f"   ✅ [ROLLBACK-SF] Attachment deletion completed: {deleted_attachments} deleted, {failed_attachments} failed")
        else:
            print(f"   ℹ️ [ROLLBACK-SF] No attachments to delete")
        
        status = "success" if failed_count == 0 and failed_attachments == 0 else ("partial_success" if deleted_count > 0 or deleted_attachments > 0 else "failed")
        
        return {
            "status": status,
            "message": f"Rollback completed: {deleted_count} records and {deleted_attachments} attachments deleted, {failed_count} records and {failed_attachments} attachments failed",
            "total_records": total_unique,
            "deleted_records": deleted_count,
            "failed_records": failed_count,
            "deleted_attachments": deleted_attachments,
            "failed_attachments": failed_attachments,
            "errors": errors
        }
    
    @staticmethod
    def _coerce_gsheets_mapped_data(mapped_data: Any) -> Dict[str, Any]:
        if isinstance(mapped_data, dict):
            return mapped_data
        if isinstance(mapped_data, str) and mapped_data.strip():
            try:
                parsed = json.loads(mapped_data)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}
    
    @staticmethod
    async def _rollback_google_sheets_records(
        records: List[Dict[str, Any]],
        user_id: str,
        mapping_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Rollback Google Sheets records (only "created" records):
        1. Filter records where salesforce_results.status = "created"
        2. Extract spreadsheet_id, sheet_name from salesforce_results
        3. Extract key_columns from mapping_config (same as used in upsert)
        4. Group by spreadsheet_id and sheet_name
        5. For each group:
           a. Read sheet data to find matching rows
           b. Match records using key_columns (same logic as upsert)
           c. Get row indices of matched rows
           d. Delete rows in batch (descending order)
        6. This ensures we delete exactly what we created
        """
        deleted_count = 0
        failed_count = 0
        errors = []
        
        print(f"🟢 [ROLLBACK-GS] Starting Google Sheets rollback for {len(records)} records")
        
        # Get Google Sheets connection for the target spreadsheet
        try:
            print(f"🔑 [ROLLBACK-GS] Fetching Google Sheets connection for user_id={user_id}")
            from utils.google_sheets_auth import get_valid_google_sheets_token
            from services.google_sheets_connection_service import connection_id_for_spreadsheet

            gs_connection_id = (mapping_config or {}).get("google_sheets_connection_id")
            sample_spreadsheet_id = None
            if records:
                sample = records[0].get("salesforce_results") or records[0].get("destination_results")
                if isinstance(sample, str):
                    try:
                        import json as _json
                        sample = _json.loads(sample)
                    except Exception:
                        sample = None
                if isinstance(sample, dict):
                    sample_spreadsheet_id = sample.get("spreadsheet_id") or sample.get("id")

            if not gs_connection_id and sample_spreadsheet_id:
                gs_connection_id = await connection_id_for_spreadsheet(
                    user_id, str(sample_spreadsheet_id)
                )
            if not gs_connection_id:
                gs_connection_id = (mapping_config or {}).get("spreadsheet_id")
                if gs_connection_id:
                    gs_connection_id = await connection_id_for_spreadsheet(
                        user_id, str(gs_connection_id)
                    )

            connection_result = await get_valid_google_sheets_token(
                user_id, str(gs_connection_id) if gs_connection_id else None
            )
            
            if not connection_result:
                print(f"❌ [ROLLBACK-GS] Google Sheets connection not found for user_id={user_id}")
                return {
                    "status": "failed",
                    "message": "Google Sheets connection not found",
                    "deleted_records": 0,
                    "failed_records": len(records),
                    "errors": ["Google Sheets connection not found"]
                }

            connection = dict(connection_result)
            sheets_service = GoogleSheetsService(
                access_token=connection["access_token"],
                refresh_token=connection.get("refresh_token"),
            )
            print(f"✅ [ROLLBACK-GS] Google Sheets connection obtained")
        except Exception as e:
            print(f"❌ [ROLLBACK-GS] Failed to get Google Sheets connection: {str(e)}")
            return {
                "status": "failed",
                "message": f"Failed to get Google Sheets connection: {str(e)}",
                "deleted_records": 0,
                "failed_records": len(records),
                "errors": [str(e)]
            }
        
        # Extract key_columns from mapping_config (same keys used for upsert duplicate detection)
        from utils.rollback_mapping_keys import extract_key_columns_from_mapping_config

        sample_mapped = records[0].get("mapped_data") if records else None
        if isinstance(sample_mapped, str):
            try:
                import json as _json

                sample_mapped = _json.loads(sample_mapped)
            except Exception:
                sample_mapped = None
        key_columns = extract_key_columns_from_mapping_config(
            mapping_config,
            destination="google_sheets",
            sample_row=sample_mapped if isinstance(sample_mapped, dict) else None,
        )
        if not key_columns and sample_mapped:
            from services.google_sheets.row_builder import infer_key_columns

            headers_guess = list(sample_mapped.keys())
            key_columns = infer_key_columns(headers_guess)
        # Group records by spreadsheet_id and sheet_name
        records_by_sheet: Dict[str, List[Dict[str, Any]]] = {}
        
        for record in records:
            salesforce_results = record.get("salesforce_results")
            if isinstance(salesforce_results, str):
                try:
                    salesforce_results = json.loads(salesforce_results)
                except Exception:
                    continue
            
            if not isinstance(salesforce_results, dict):
                continue

            mapped_raw = record.get("mapped_data", {})
            mapped_data = RollbackService._coerce_gsheets_mapped_data(mapped_raw)
            
            for spreadsheet_name, result in salesforce_results.items():
                if isinstance(result, dict) and result.get("status") == "created":
                    spreadsheet_id = result.get("id")  # This is the spreadsheet_id
                    sheet_name = result.get("sheet_name")
                    
                    if spreadsheet_id and sheet_name:
                        key = f"{spreadsheet_id}::{sheet_name}"
                        if key not in records_by_sheet:
                            records_by_sheet[key] = []
                        records_by_sheet[key].append({
                            "migration_record_id": record.get("id"),
                            "mapped_data": mapped_data,
                            "spreadsheet_id": spreadsheet_id,
                            "sheet_name": sheet_name
                        })
        
        # Process each sheet group
        migration_service = GoogleSheetsMigrationService()
        
        for sheet_key, record_list in records_by_sheet.items():
            spreadsheet_id = record_list[0]["spreadsheet_id"]
            sheet_name = record_list[0]["sheet_name"]
            
            print(f"🗑️ [ROLLBACK-GS] Processing sheet: {sheet_name} in spreadsheet_id={spreadsheet_id}")
            try:
                # Use the migration service to find and delete rows
                result = await migration_service.find_and_delete_rows(
                    sheets_service=sheets_service,
                    spreadsheet_id=spreadsheet_id,
                    sheet_name=sheet_name,
                    records_to_delete=[r["mapped_data"] for r in record_list],
                    key_columns=key_columns,
                )
                
                deleted_count += result.get("deleted_count", 0)
                failed_count += result.get("failed_count", 0)
                errors.extend(result.get("errors", []))
                
                # Update migration record statuses
                for record_info in record_list:
                    if result.get("deleted_count", 0) > 0:
                        await MigrationRecordsService.update_migration_record_status(
                            record_id=record_info["migration_record_id"],
                            status="rolled_back"
                        )
            except Exception as e:
                error_msg = f"Failed to delete rows from {sheet_name} in {spreadsheet_id}: {str(e)}"
                errors.append(error_msg)
                failed_count += len(record_list)
                print(f"   ❌ [ROLLBACK-GS] {error_msg}")
        
        print(f"📊 [ROLLBACK-GS] Rollback summary: deleted={deleted_count}, failed={failed_count}")
        status = "success" if failed_count == 0 else ("partial_success" if deleted_count > 0 else "failed")
        
        return {
            "status": status,
            "message": f"Rollback completed: {deleted_count} deleted, {failed_count} failed",
            "total_records": len(records),
            "deleted_records": deleted_count,
            "failed_records": failed_count,
            "errors": errors
        }
    
    @staticmethod
    def _scoped_sql_mapped_row_for_table(mapped_data: Dict[str, Any], table_name: str) -> Dict[str, Any]:
        """Turn flattened migration mapped_data ('users.email') into per-table column dict."""
        out: Dict[str, Any] = {}
        if not mapped_data or not table_name:
            return out
        md = mapped_data if isinstance(mapped_data, dict) else {}
        tn_l = table_name.strip().lower()

        nested = md.get(table_name)
        if nested is None:
            for nk, nv in md.items():
                if isinstance(nk, str) and nk.lower() == tn_l and isinstance(nv, dict):
                    nested = nv
                    break
        if isinstance(nested, dict):
            out.update(nested)

        for k, v in md.items():
            if not isinstance(k, str) or "." not in k:
                continue
            tpart, _, col = k.partition(".")
            if tpart.strip().lower() == tn_l and col:
                out[col] = v
        return out

    @staticmethod
    def _infer_sql_rollback_key_columns(rows: List[Dict[str, Any]]) -> List[str]:
        """Columns that are non-null for every row (safe AND clause for bulk DELETE)."""
        if not rows:
            return []
        sets = [{k for k, v in r.items() if v is not None} for r in rows]
        common = set.intersection(*sets) if sets else set()
        if common:
            return list(common)
        return [k for k, v in rows[0].items() if v is not None]

    @staticmethod
    async def _rollback_sql_records(
        records: List[Dict[str, Any]],
        user_id: str,
        mapping_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Rollback SQL database records (only "created" entries in salesforce_results).

        Supports lookup migrations: deletes per destination table using scoped mapped_data
        (table.column flattening). Deletes children before parents when lookup_tables_order
        is stored on the batch (reverse topological order).
        """
        deleted_count = 0
        failed_count = 0
        errors: List[str] = []

        print(f"🟡 [ROLLBACK-SQL] Starting SQL rollback for {len(records)} records")

        connector_id = mapping_config.get("connector_id")
        legacy_table_name = mapping_config.get("table_name")
        lookup_migration = bool(mapping_config.get("lookup_migration"))
        topo_order = mapping_config.get("lookup_tables_order")

        if not connector_id:
            print(f"❌ [ROLLBACK-SQL] Missing connector_id in mapping_config")
            return {
                "status": "failed",
                "message": "Missing connector_id in mapping_config",
                "deleted_records": 0,
                "failed_records": len(records),
                "errors": ["Missing connector_id"],
            }

        if lookup_migration and not topo_order:
            print(
                "⚠️ [ROLLBACK-SQL] lookup_migration batch has no lookup_tables_order; "
                "FK-safe delete order is not guaranteed for older batches"
            )

        by_table: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        migration_ids_with_created = set()

        for record in records:
            salesforce_results = record.get("salesforce_results")
            if isinstance(salesforce_results, str):
                try:
                    salesforce_results = json.loads(salesforce_results)
                except Exception:
                    continue
            if not isinstance(salesforce_results, dict):
                continue

            mapped_data = record.get("mapped_data", {})
            if isinstance(mapped_data, str):
                try:
                    mapped_data = json.loads(mapped_data)
                except Exception:
                    mapped_data = {}
            if not isinstance(mapped_data, dict):
                mapped_data = {}

            mid = record.get("id")
            for table_key, result in salesforce_results.items():
                if isinstance(result, dict) and result.get("status") == "created":
                    migration_ids_with_created.add(mid)
                    scoped = RollbackService._scoped_sql_mapped_row_for_table(mapped_data, table_key)
                    if not scoped and isinstance(mapped_data, dict):
                        has_prefixed = any(
                            isinstance(k, str) and "." in k for k in mapped_data.keys()
                        )
                        if not has_prefixed:
                            scoped = dict(mapped_data)
                    by_table[table_key].append(
                        {"migration_record_id": mid, "scoped": scoped}
                    )

        total_payload_rows = sum(len(v) for v in by_table.values())
        print(
            f"🔧 [ROLLBACK-SQL] Connector: connector_id={connector_id}, "
            f"lookup_migration={lookup_migration}, tables_with_creates={list(by_table.keys())}, "
            f"payload_rows={total_payload_rows}"
        )

        if not by_table:
            print(f"ℹ️ [ROLLBACK-SQL] No created table entries to rollback")
            return {
                "status": "success",
                "message": "No created records found to rollback",
                "deleted_records": 0,
                "failed_records": 0,
                "errors": [],
            }

        if lookup_migration and topo_order:
            ordered_tables = [t for t in reversed(topo_order) if t in by_table]
            for t in by_table:
                if t not in ordered_tables:
                    ordered_tables.append(t)
        elif legacy_table_name and legacy_table_name in by_table:
            ordered_tables = [legacy_table_name] + [t for t in by_table if t != legacy_table_name]
        else:
            ordered_tables = list(by_table.keys())

        migration_service = DatabaseMigrationService()

        try:
            print(f"🗑️ [ROLLBACK-SQL] Deleting in order: {ordered_tables}")
            for tbl in ordered_tables:
                entries = by_table[tbl]
                rows = [e["scoped"] for e in entries]
                from utils.rollback_mapping_keys import extract_key_columns_from_mapping_config

                key_columns = extract_key_columns_from_mapping_config(
                    mapping_config,
                    destination="sql",
                    sample_row=rows[0] if rows else None,
                )
                if not key_columns:
                    key_columns = RollbackService._infer_sql_rollback_key_columns(rows)
                if not key_columns:
                    msg = f"No non-null columns to match for rollback on table {tbl!r}"
                    errors.append(msg)
                    failed_count += len(entries)
                    print(f"❌ [ROLLBACK-SQL] {msg}")
                    continue

                print(
                    f"   🗑️ [ROLLBACK-SQL] table={tbl!r} rows={len(rows)} "
                    f"key_columns={key_columns!r}"
                )

                result = await migration_service.find_and_delete_created_records(
                    connector_id=connector_id,
                    table_name=tbl,
                    records_to_delete=rows,
                    key_columns=key_columns,
                    user_id=user_id,
                )
                deleted_count += int(result.get("deleted_count", 0))
                failed_count += int(result.get("failed_count", 0))
                errors.extend(result.get("errors", []))
                print(
                    f"   📊 [ROLLBACK-SQL] table={tbl!r} deleted={result.get('deleted_count')}, "
                    f"failed={result.get('failed_count')}"
                )

            if failed_count == 0:
                for mid in migration_ids_with_created:
                    if mid:
                        await MigrationRecordsService.update_migration_record_status(
                            record_id=mid,
                            status="rolled_back",
                        )
            else:
                print(
                    f"⚠️ [ROLLBACK-SQL] Skipping migration_record status update "
                    f"(failures={failed_count})"
                )
        except Exception as e:
            error_msg = f"Failed to delete SQL records: {str(e)}"
            errors.append(error_msg)
            failed_count += total_payload_rows
            print(f"❌ [ROLLBACK-SQL] {error_msg}")
            import traceback

            traceback.print_exc()

        print(f"📊 [ROLLBACK-SQL] Rollback summary: deleted={deleted_count}, failed={failed_count}")
        status = "success" if failed_count == 0 else ("partial_success" if deleted_count > 0 else "failed")

        return {
            "status": status,
            "message": f"Rollback completed: {deleted_count} deleted, {failed_count} failed",
            "total_records": len(records),
            "deleted_records": deleted_count,
            "failed_records": failed_count,
            "errors": errors,
        }
