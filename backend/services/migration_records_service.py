import uuid
import json
import re
from collections import Counter
from datetime import datetime
from typing import AsyncIterator, List, Dict, Any, Optional
from db.connection import database
from db.models import migration_batches, migration_records
from utils.extraction_record_display import strip_row_display_metadata


class MigrationRecordsService:
    """Service for managing migration records and batches in the database"""

    FAILED_RECORD_STATUSES = ("failed", "error", "create_failed", "update_failed")
    TERMINAL_BATCH_STATUSES = frozenset({"completed", "failed"})

    @staticmethod
    def _extract_fields_from_error(message: str) -> List[str]:
        if not message:
            return []
        fields: List[str] = []
        bracket = re.search(r"\[([^\]]+)\]", message)
        if bracket:
            for part in bracket.group(1).split(","):
                token = part.strip()
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token):
                    fields.append(token)
        for match in re.finditer(
            r"(?:INVALID_FIELD(?:_FOR_INSERT_UPDATE)?|Field)\s*[:\s]+([A-Za-z_][A-Za-z0-9_]*)",
            message,
            re.IGNORECASE,
        ):
            fields.append(match.group(1))
        return fields

    @staticmethod
    async def get_migration_batch_status(batch_id: str) -> Optional[str]:
        row = await database.fetch_one(
            """
            SELECT status FROM migration_batches WHERE id = :batch_id LIMIT 1
            """,
            values={"batch_id": batch_id},
        )
        if not row:
            return None
        return str(dict(row).get("status") or "").strip() or None

    @staticmethod
    async def summarize_batch_failures(
        batch_id: str,
        *,
        limit: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Aggregate failed migration records into fields + error samples for learning/RAG."""
        try:
            batch_row = await database.fetch_one(
                """
                SELECT
                    id,
                    user_id,
                    destination_type,
                    document_type,
                    total_records,
                    successful_records,
                    failed_records,
                    status,
                    detail_purged_at
                FROM migration_batches
                WHERE id = :batch_id
                LIMIT 1
                """,
                values={"batch_id": batch_id},
            )
            if not batch_row:
                return None

            batch = dict(batch_row)
            batch_status = (batch.get("status") or "in_progress").strip().lower()
            batch_terminal = batch_status in MigrationRecordsService.TERMINAL_BATCH_STATUSES

            if batch.get("detail_purged_at"):
                total = int(batch.get("total_records") or 0)
                ok = int(batch.get("successful_records") or 0)
                fail = int(batch.get("failed_records") or 0)
                denom = max(total, ok + fail, 1)
                return {
                    "batch_id": batch_id,
                    "batch_status": batch_status,
                    "ready_for_recovery": batch_terminal,
                    "destination_type": batch.get("destination_type"),
                    "destination_object": batch.get("document_type"),
                    "total_records": denom,
                    "successful_records": ok,
                    "failed_records": fail,
                    "success_rate": ok / denom,
                    "failed_fields": [],
                    "error_samples": [],
                    "top_errors": [],
                    "detail_expired": True,
                }

            if not batch_terminal:
                total = int(batch.get("total_records") or 0)
                ok = int(batch.get("successful_records") or 0)
                fail = int(batch.get("failed_records") or 0)
                return {
                    "batch_id": batch_id,
                    "batch_status": batch_status,
                    "ready_for_recovery": False,
                    "destination_type": batch.get("destination_type"),
                    "destination_object": batch.get("document_type"),
                    "total_records": total,
                    "successful_records": ok,
                    "failed_records": fail,
                    "success_rate": (ok / max(total, ok + fail, 1)) if total else 0.0,
                    "failed_fields": [],
                    "error_samples": [],
                    "top_errors": [],
                    "detail_expired": False,
                }

            status_clause = ", ".join(f"'{s}'" for s in MigrationRecordsService.FAILED_RECORD_STATUSES)
            failed_rows = await database.fetch_all(
                f"""
                SELECT record_index, error_message, salesforce_results
                FROM migration_records
                WHERE batch_id = :batch_id
                  AND status IN ({status_clause})
                ORDER BY record_index ASC
                LIMIT :limit
                """,
                values={"batch_id": batch_id, "limit": max(1, min(limit, 100))},
            )

            error_messages: List[str] = []
            field_counter: Counter = Counter()
            for row in failed_rows or []:
                rec = dict(row)
                msg = (rec.get("error_message") or "").strip()
                if not msg and rec.get("salesforce_results"):
                    try:
                        results = rec["salesforce_results"]
                        if isinstance(results, str):
                            results = json.loads(results)
                        if isinstance(results, dict):
                            for val in results.values():
                                if isinstance(val, dict) and val.get("message"):
                                    msg = str(val["message"])
                                    break
                                if isinstance(val, str) and val:
                                    msg = val
                                    break
                    except (json.JSONDecodeError, TypeError):
                        pass
                if msg:
                    error_messages.append(msg[:500])
                    for field in MigrationRecordsService._extract_fields_from_error(msg):
                        field_counter[field] += 1

            counts = await database.fetch_one(
                f"""
                SELECT
                    COUNT(*) AS total_created,
                    COUNT(CASE WHEN status IN ('success', 'created', 'updated') THEN 1 END) AS ok,
                    COUNT(CASE WHEN status IN ({status_clause}) THEN 1 END) AS fail
                FROM migration_records
                WHERE batch_id = :batch_id
                """,
                values={"batch_id": batch_id},
            )
            count_dict = dict(counts) if counts else {}
            ok = int(
                batch.get("successful_records")
                if batch.get("successful_records") is not None
                else count_dict.get("ok") or 0
            )
            fail = int(
                batch.get("failed_records")
                if batch.get("failed_records") is not None
                else count_dict.get("fail") or 0
            )
            total = int(batch.get("total_records") or count_dict.get("total_created") or 0)
            total = max(total, ok + fail, 1)
            rate = ok / total

            msg_counter = Counter(error_messages)
            top_errors = [
                {
                    "message": msg,
                    "count": cnt,
                }
                for msg, cnt in msg_counter.most_common(5)
            ]

            return {
                "batch_id": batch_id,
                "batch_status": batch_status,
                "ready_for_recovery": True,
                "destination_type": batch.get("destination_type"),
                "destination_object": batch.get("document_type"),
                "total_records": total,
                "successful_records": ok,
                "failed_records": fail,
                "success_rate": rate,
                "failed_fields": [f for f, _ in field_counter.most_common(15)],
                "error_samples": error_messages[:10],
                "top_errors": top_errors,
                "detail_expired": False,
            }
        except Exception as exc:
            print(f"❌ summarize_batch_failures failed for {batch_id}: {exc}")
            return None

    @staticmethod
    async def get_failed_records_for_recovery(
        batch_id: str,
        *,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Load failed migration rows with payloads for recovery classification."""
        try:
            status_clause = ", ".join(
                f"'{s}'" for s in MigrationRecordsService.FAILED_RECORD_STATUSES
            )
            rows = await database.fetch_all(
                f"""
                SELECT
                    record_index,
                    error_message,
                    source_data,
                    mapped_data,
                    salesforce_results,
                    status
                FROM migration_records
                WHERE batch_id = :batch_id
                  AND status IN ({status_clause})
                ORDER BY record_index ASC
                LIMIT :limit
                """,
                values={"batch_id": batch_id, "limit": max(1, min(limit, 2000))},
            )
            out: List[Dict[str, Any]] = []
            for row in rows or []:
                rec = dict(row)
                for key in ("source_data", "mapped_data", "salesforce_results"):
                    raw = rec.get(key)
                    if isinstance(raw, str) and raw.strip():
                        try:
                            rec[key] = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            pass
                out.append(rec)
            return out
        except Exception as exc:
            print(f"❌ get_failed_records_for_recovery failed for {batch_id}: {exc}")
            return []

    @staticmethod
    def _sanitize_row_payload(data: Any) -> Any:
        """Remove extraction-rule blobs from row payloads before persist/API (not data columns)."""
        if isinstance(data, dict):
            return strip_row_display_metadata(data)
        return data

    @staticmethod
    async def create_migration_batch(
        user_id: str,
        batch_name: str,
        document_type: str,
        total_records: int,
        mapping_config: Dict[str, Any],
        destination_type: str = "salesforce",  # Default to salesforce for backwards compatibility
        batch_type: str = "data",  # 'data' = record migration, 'metadata' = metadata/field deploy
    ) -> str:
        """Create a new migration batch and return the batch ID"""
        batch_id = str(uuid.uuid4())
        # Convert mapping_config to JSON string for storage
        try:
            import json
            mapping_config_json = json.dumps(mapping_config)
        except Exception as e:
            print(f"❌ mapping_config is NOT JSON serializable: {e}")
            mapping_config_json = json.dumps({"error": "Could not serialize mapping config", "original_type": str(type(mapping_config))})
        
        query = """
        INSERT INTO migration_batches (
            id, user_id, batch_name, document_type, batch_type, total_records,
            destination_type, mapping_config, created_at
        ) VALUES (
            :batch_id, :user_id, :batch_name, :document_type, :batch_type, :total_records,
            :destination_type, :mapping_config, :created_at
        )
        """
        
        values = {
            "batch_id": batch_id,
            "user_id": user_id,
            "batch_name": batch_name,
            "document_type": document_type,
            "batch_type": batch_type,
            "total_records": total_records,
            "destination_type": destination_type,
            "mapping_config": mapping_config_json,  # Store as JSON string
            "created_at": datetime.utcnow()
        }
        
        try:
            await database.execute(query, values=values)
        except Exception as e:
            print(f"❌ Database insert failed: {e}")
            print(f"❌ Error type: {type(e)}")
            raise
        
        return batch_id
    
    @staticmethod
    async def create_migration_record(
        batch_id: str,
        user_id: str,
        record_index: int,
        source_data: Dict[str, Any],
        mapped_data: Dict[str, Any]
    ) -> str:
        """Create a new migration record and return the record ID"""
        record_id = str(uuid.uuid4())
        source_data = MigrationRecordsService._sanitize_row_payload(source_data)
        mapped_data = MigrationRecordsService._sanitize_row_payload(mapped_data)

        # Convert data to JSON strings for storage
        try:
            import json
            source_data_json = json.dumps(source_data)
            mapped_data_json = json.dumps(mapped_data)
        except Exception as e:
            print(f"❌ Failed to convert record data to JSON: {e}")
            source_data_json = json.dumps({"error": "Could not serialize source data"})
            mapped_data_json = json.dumps({"error": "Could not serialize mapped data"})
        
        query = """
        INSERT INTO migration_records (
            id, batch_id, user_id, record_index, source_data, mapped_data, 
            status, created_at
        ) VALUES (
            :record_id, :batch_id, :user_id, :record_index, :source_data, :mapped_data,
            :status, :created_at
        )
        """
        
        values = {
            "record_id": record_id,
            "batch_id": batch_id,
            "user_id": user_id,
            "record_index": record_index,
            "source_data": source_data_json,
            "mapped_data": mapped_data_json,
            "status": "pending",
            "created_at": datetime.utcnow()
        }
        
        try:
            await database.execute(query, values=values)
        except Exception as e:
            print(f"❌ Failed to create migration record: {e}")
            raise
        
        return record_id
    
    @staticmethod
    async def update_migration_record_status(
        record_id: str,
        status: str,
        salesforce_results: Dict[str, Any] = None,
        error_message: str = None
    ) -> bool:
        """Update the status of a migration record"""
        try:
            # Prepare the update query
            update_fields = ["status = :status"]
            values = {
                "record_id": record_id,
                "status": status
            }
            
            if salesforce_results is not None:
                update_fields.append("salesforce_results = :salesforce_results")
                values["salesforce_results"] = json.dumps(salesforce_results)
            
            if error_message is not None:
                update_fields.append("error_message = :error_message")
                values["error_message"] = error_message
            
            if status == "success":
                update_fields.append("processed_at = :processed_at")
                values["processed_at"] = datetime.utcnow()
            elif status == "failed":
                update_fields.append("processed_at = :processed_at")
                values["processed_at"] = datetime.utcnow()
            
            query = f"""
            UPDATE migration_records 
            SET {', '.join(update_fields)}
            WHERE id = :record_id
            """
            
            await database.execute(query, values=values)
            return True
            
        except Exception as e:
            print(f"❌ Failed to update migration record status: {e}")
            return False
    
    @staticmethod
    async def update_migration_record_result(
        record_id: str,
        status: str,
        salesforce_results: Dict[str, Any] = None,
        error_message: str = None
    ) -> bool:
        """Update the result of a migration record - alias for update_migration_record_status"""
        return await MigrationRecordsService.update_migration_record_status(
            record_id=record_id,
            status=status,
            salesforce_results=salesforce_results,
            error_message=error_message
        )
    
    @staticmethod
    async def update_batch_status(
        batch_id: str,
        status: str,
        error_summary: str = None,
        rollback_summary: str = None,
        successful_count: int = None,
        failed_count: int = None,
        total_records: int = None,
        conversation_id: Optional[str] = None,
        destination_name: Optional[str] = None,
    ) -> bool:
        """Update the status of a migration batch"""
        try:
            update_fields = ["status = :status"]
            values = {
                "batch_id": batch_id,
                "status": status
            }
            
            if status == "completed":
                update_fields.append("completed_at = :completed_at")
                values["completed_at"] = datetime.utcnow()
            
            if error_summary is not None:
                update_fields.append("error_summary = :error_summary")
                values["error_summary"] = error_summary
            
            if rollback_summary is not None:
                update_fields.append("rollback_summary = :rollback_summary")
                values["rollback_summary"] = rollback_summary
                
            if successful_count is not None:
                update_fields.append("successful_records = :successful_records")
                values["successful_records"] = successful_count
                
            if failed_count is not None:
                update_fields.append("failed_records = :failed_records")
                values["failed_records"] = failed_count

            if total_records is not None:
                update_fields.append("total_records = :total_records")
                values["total_records"] = total_records
            
            query = f"""
            UPDATE migration_batches 
            SET {', '.join(update_fields)}
            WHERE id = :batch_id
            """
            
            await database.execute(query, values=values)
            if status == "completed":
                try:
                    batch_row = await database.fetch_one(
                        """
                        SELECT user_id, total_records, successful_records, failed_records,
                               destination_type, document_type
                        FROM migration_batches WHERE id = :batch_id
                        """,
                        values={"batch_id": batch_id},
                    )
                    if batch_row:
                        from services.migration.learning_capture import (
                            schedule_migration_learning_for_batch,
                        )

                        br = dict(batch_row)
                        total = int(br.get("total_records") or total_records or 0)
                        ok = int(
                            br.get("successful_records")
                            if successful_count is None
                            else successful_count
                        )
                        fail = int(
                            br.get("failed_records")
                            if failed_count is None
                            else failed_count
                        )
                        await schedule_migration_learning_for_batch(
                            batch_id,
                            user_id=str(br["user_id"]),
                            conversation_id=conversation_id,
                            destination_type=br.get("destination_type"),
                            destination_name=destination_name,
                            destination_object=br.get("document_type"),
                            total_records=max(total, ok + fail, 1),
                            successful_records=ok,
                            failed_records=fail,
                        )
                except Exception:
                    pass
            return True
            
        except Exception as e:
            print(f"❌ Failed to update migration batch status: {e}")
            return False

    @staticmethod
    async def get_migration_batch_summary(batch_id: str) -> Dict[str, Any]:
        """Get a summary of a migration batch including record counts by status"""
        try:
            query = """
            SELECT 
                mb.id,
                mb.batch_name,
                mb.document_type,
                mb.total_records,
                mb.created_at,
                mb.detail_purged_at,
                COUNT(mr.id) as total_records_created,
                COUNT(CASE WHEN mr.status = 'success' THEN 1 END) as success_count,
                COUNT(CASE WHEN mr.status = 'failed' THEN 1 END) as failed_count,
                COUNT(CASE WHEN mr.status = 'pending' THEN 1 END) as pending_count
            FROM migration_batches mb
            LEFT JOIN migration_records mr ON mb.id = mr.batch_id
            WHERE mb.id = :batch_id
            GROUP BY mb.id, mb.batch_name, mb.document_type, mb.total_records, mb.created_at, mb.detail_purged_at
            """
            
            result = await database.fetch_one(query, values={"batch_id": batch_id})
            
            if result:
                summary = dict(result)
                summary["created_at"] = summary["created_at"].isoformat() if summary["created_at"] else None
                dp = summary.get("detail_purged_at")
                had_purge = dp is not None
                if had_purge and hasattr(dp, "isoformat"):
                    summary["detail_purged_at"] = dp.isoformat()
                summary["detail_available"] = not had_purge
                summary["detail_expired"] = had_purge
                return summary
            else:
                return None
                
        except Exception as e:
            print(f"❌ Error getting migration batch summary: {e}")
            return None
    
    @staticmethod
    async def get_latest_successful_migrations(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the latest successful migration records for a user"""
        query = """
        SELECT 
            mr.id,
            mr.record_index,
            mr.source_data,
            mr.mapped_data,
            mr.salesforce_results,
            mr.status,
            mr.created_at,
            mr.processed_at,
            mb.destination_type,
            mb.batch_type,
            mb.mapping_config
        FROM migration_records mr
        LEFT JOIN migration_batches mb ON mr.batch_id = mb.id
        WHERE mr.user_id = :user_id 
        AND mr.status = 'success'
        ORDER BY mr.processed_at DESC, mr.created_at DESC
        LIMIT :limit
        """
        
        records = await database.fetch_all(query, values={"user_id": user_id, "limit": limit})
        
        # Process the records to extract useful information
        processed_records = []
        for record in records:
            record_dict = dict(record)
            
            # Send the complete salesforce_results to frontend for handling
            salesforce_results = record_dict.get('salesforce_results')
            
            destination_type = record_dict.get("destination_type") or "salesforce"
            destination_name = MigrationRecordsService._extract_destination_name(
                destination_type,
                record_dict.get("mapping_config")
            )

            processed_records.append({
                "id": record_dict["id"],
                "record_index": record_dict["record_index"],
                "salesforce_results": salesforce_results,  # Send complete results
                "migrated_at": (record_dict["processed_at"] or record_dict["created_at"]).isoformat() if record_dict["processed_at"] or record_dict["created_at"] else None,
                "source_data": record_dict["source_data"],
                "status": "success",
                "destination_type": destination_type,
                "destination_name": destination_name,
                "batch_type": record_dict.get("batch_type") or "data",
            })
        
        return processed_records

    @staticmethod
    async def get_recent_failed_migrations(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get the recent failed migration records for a user"""
        query = """
        SELECT 
            mr.id,
            mr.record_index,
            mr.source_data,
            mr.mapped_data,
            mr.salesforce_results,
            mr.status,
            mr.error_message,
            mr.created_at,
            mr.processed_at,
            mb.destination_type,
            mb.mapping_config
        FROM migration_records mr
        LEFT JOIN migration_batches mb ON mr.batch_id = mb.id
        WHERE mr.user_id = :user_id 
        AND mr.status IN ('failed', 'error')
        ORDER BY mr.created_at DESC
        LIMIT :limit
        """
        
        records = await database.fetch_all(query, values={"user_id": user_id, "limit": limit})
        
        # Process the records to extract useful information
        processed_records = []
        for record in records:
            record_dict = dict(record)
            
            # Extract object types from salesforce_results (same logic as successful migrations)
            object_types = []
            salesforce_results = record_dict.get('salesforce_results')
            
            if salesforce_results:
                try:
                    import json
                    results = json.loads(salesforce_results) if isinstance(salesforce_results, str) else salesforce_results
                    if isinstance(results, dict):
                        # Extract object types from the keys (excluding 'error')
                        object_types = [key for key in results.keys() if key != 'error']
                except Exception as e:
                    print(f"Error parsing salesforce_results for failed record {record_dict['id']}: {e}")
                    object_types = []
            
            # Join multiple object types with comma, or use None if none found
            object_type = ', '.join(object_types) if object_types else None
            
            destination_type = record_dict.get("destination_type") or "salesforce"
            destination_name = MigrationRecordsService._extract_destination_name(
                destination_type,
                record_dict.get("mapping_config")
            )

            processed_records.append({
                "id": record_dict["id"],
                "record_id": record_dict["record_index"],
                "object_type": object_type,
                "salesforce_results": salesforce_results,
                "error_message": record_dict["error_message"],
                "failed_at": (record_dict["processed_at"] or record_dict["created_at"]).isoformat() if record_dict["processed_at"] or record_dict["created_at"] else None,
                "created_at": record_dict["created_at"].isoformat() if record_dict["created_at"] else None,
                "source_data": record_dict["source_data"],
                "status": "failed",
                "destination_type": destination_type,  # Default to salesforce for backwards compatibility
                "destination_name": destination_name
            })
        
        return processed_records

    @staticmethod
    async def resolve_destination_view_context(
        user_id: str,
        destination_type: str,
        mapping_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Optional[str]]:
        """
        Resolve Salesforce instance_url and Google Sheets spreadsheet_id for "View record" links
        when loading migration results outside of chat (metadata may only store org_id, not full URL).
        """
        out: Dict[str, Optional[str]] = {"instance_url": None, "spreadsheet_id": None}
        mc = mapping_config if isinstance(mapping_config, dict) else {}
        dt = (destination_type or "salesforce").lower()

        if dt == "salesforce":
            direct = mc.get("instance_url")
            if direct:
                out["instance_url"] = str(direct).rstrip("/")
                return out
            org_id = mc.get("org_id")
            if not org_id:
                return out
            cid = mc.get("connected_app_id")
            if cid is not None:
                row = await database.fetch_one(
                    """
                    SELECT instance_url FROM salesforce_tokens
                    WHERE user_id = :user_id AND org_id = :org_id
                      AND connected_app_id = :cid
                    ORDER BY issued_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "org_id": str(org_id), "cid": cid},
                )
            else:
                row = await database.fetch_one(
                    """
                    SELECT instance_url FROM salesforce_tokens
                    WHERE user_id = :user_id AND org_id = :org_id
                    ORDER BY issued_at DESC NULLS LAST
                    LIMIT 1
                    """,
                    {"user_id": str(user_id), "org_id": str(org_id)},
                )
            if row:
                rd = dict(row)
                iu = rd.get("instance_url")
                if iu:
                    out["instance_url"] = str(iu).rstrip("/")
            return out

        if dt == "google_sheets":
            sid = mc.get("spreadsheet_id") or mc.get("destination_spreadsheet_id")
            if sid:
                out["spreadsheet_id"] = str(sid)
        return out

    @staticmethod
    def _extract_destination_name(destination_type: str, mapping_config: Any) -> Optional[str]:
        if not mapping_config:
            return None

        try:
            if isinstance(mapping_config, str):
                mapping = json.loads(mapping_config)
            else:
                mapping = mapping_config
        except Exception:
            return None

        if not isinstance(mapping, dict):
            return None

        destination_type = (destination_type or "salesforce").lower()

        if destination_type == "google_sheets":
            return (
                mapping.get("destination_name")
                or mapping.get("spreadsheet_name")
                or mapping.get("spreadsheet_id")
            )

        if destination_type == "sql":
            return (
                mapping.get("destination_name")
                or mapping.get("connector_name")
                or mapping.get("connector_id")
            )

        # Salesforce or other destinations
        return (
            mapping.get("destination_name")
            or mapping.get("connection_name")
            or mapping.get("org_name")
        )

    @staticmethod
    async def _get_sync_outcome_for_batch(batch_id: str, user_id: str) -> Optional[str]:
        """Resolve run-level sync outcome from flow_executions for this batch."""
        try:
            row = await database.fetch_one(
                """
                SELECT sync_outcome
                FROM flow_executions
                WHERE migration_batch_id = :batch_id
                  AND user_id = :user_id
                ORDER BY started_at DESC NULLS LAST, created_at DESC NULLS LAST
                LIMIT 1
                """,
                values={"batch_id": batch_id, "user_id": user_id},
            )
            if not row:
                return None
            return dict(row).get("sync_outcome")
        except Exception as e:
            print(f"⚠️ Failed to resolve sync_outcome for batch {batch_id}: {e}")
            return None

    @staticmethod
    async def get_all_successful_migrations(user_id: str) -> List[Dict[str, Any]]:
        """Get all successful migration records for a user (legacy - no pagination). Use get_all_successful_migrations_paginated for large datasets."""
        result = await MigrationRecordsService.get_all_successful_migrations_paginated(
            user_id=user_id, page=1, page_size=100000
        )
        return result["records"] if result else []

    @staticmethod
    async def get_all_successful_migrations_paginated(
        user_id: str,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get successful migration records with server-side pagination and optional search."""
        try:
            page = max(1, page)
            page_size = min(max(1, page_size), 100)
            offset = (page - 1) * page_size

            base_where = """
            FROM migration_records mr
            LEFT JOIN migration_batches mb ON mr.batch_id = mb.id
            WHERE mr.user_id = :user_id
            AND mr.status = 'success'
            """
            search_clause = ""
            values: Dict[str, Any] = {"user_id": user_id}

            if search and search.strip():
                search_pattern = f"%{search.strip()}%"
                search_clause = """
                AND (
                    mr.source_data::text ILIKE :search_pattern
                    OR mr.salesforce_results::text ILIKE :search_pattern
                )
                """
                values["search_pattern"] = search_pattern

            count_query = f"SELECT COUNT(*) as total {base_where} {search_clause}"
            count_row = await database.fetch_one(count_query, values=values)
            total_count = count_row["total"] if count_row else 0

            data_query = f"""
            SELECT
                mr.id,
                mr.record_index,
                mr.source_data,
                mr.mapped_data,
                mr.salesforce_results,
                mr.status,
                mr.created_at,
                mr.processed_at,
                mr.batch_id,
                mb.destination_type,
                mb.batch_type,
                mb.mapping_config
            {base_where}
            {search_clause}
            ORDER BY mr.processed_at DESC, mr.created_at DESC
            LIMIT :limit OFFSET :offset
            """
            values["limit"] = page_size
            values["offset"] = offset
            records = await database.fetch_all(data_query, values=values)

            processed_records = []
            for record in records:
                record_dict = dict(record)
                source_data = record_dict.get("source_data")
                salesforce_results = record_dict.get("salesforce_results")
                object_types = []
                if salesforce_results:
                    try:
                        results = json.loads(salesforce_results) if isinstance(salesforce_results, str) else salesforce_results
                        if isinstance(results, dict):
                            object_types = [key for key in results.keys() if key != "error"]
                    except Exception as e:
                        print(f"Error parsing salesforce_results for record {record_dict['id']}: {e}")

                destination_type = record_dict.get("destination_type") or "salesforce"
                destination_name = MigrationRecordsService._extract_destination_name(
                    destination_type,
                    record_dict.get("mapping_config"),
                )

                processed_records.append({
                    "id": record_dict["id"],
                    "record_index": record_dict["record_index"],
                    "object_type": ", ".join(object_types) if object_types else None,
                    "salesforce_results": salesforce_results,
                    "migrated_at": (record_dict["processed_at"] or record_dict["created_at"]).isoformat() if record_dict.get("processed_at") or record_dict.get("created_at") else None,
                    "source_data": source_data,
                    "status": "success",
                    "destination_type": destination_type,
                    "destination_name": destination_name,
                    "batch_type": record_dict.get("batch_type") or "data",
                })

            return {
                "records": processed_records,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
            }

        except Exception as e:
            print(f"❌ Error in get_all_successful_migrations_paginated: {e}")
            raise

    @staticmethod
    async def get_all_failed_migrations(user_id: str) -> List[Dict[str, Any]]:
        """Legacy - use get_all_failed_migrations_paginated for large datasets."""
        result = await MigrationRecordsService.get_all_failed_migrations_paginated(
            user_id=user_id, page=1, page_size=100000
        )
        return result["records"] if result else []

    @staticmethod
    async def get_all_failed_migrations_paginated(
        user_id: str,
        page: int = 1,
        page_size: int = 50,
        search: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get failed migration records with server-side pagination and optional search."""
        try:
            page = max(1, page)
            page_size = min(max(1, page_size), 100)
            offset = (page - 1) * page_size

            base_where = """
            FROM migration_records mr
            LEFT JOIN migration_batches mb ON mr.batch_id = mb.id
            WHERE mr.user_id = :user_id
            AND mr.status IN ('failed', 'error')
            """
            search_clause = ""
            values: Dict[str, Any] = {"user_id": user_id}

            if search and search.strip():
                search_pattern = f"%{search.strip()}%"
                search_clause = """
                AND (
                    mr.source_data::text ILIKE :search_pattern
                    OR mr.salesforce_results::text ILIKE :search_pattern
                    OR COALESCE(mr.error_message, '') ILIKE :search_pattern
                )
                """
                values["search_pattern"] = search_pattern

            count_query = f"SELECT COUNT(*) as total {base_where} {search_clause}"
            count_row = await database.fetch_one(count_query, values=values)
            total_count = count_row["total"] if count_row else 0

            data_query = f"""
            SELECT
                mr.id,
                mr.record_index,
                mr.source_data,
                mr.mapped_data,
                mr.salesforce_results,
                mr.status,
                mr.error_message,
                mr.created_at,
                mr.processed_at,
                mr.batch_id,
                mb.destination_type,
                mb.mapping_config
            {base_where}
            {search_clause}
            ORDER BY mr.processed_at DESC NULLS LAST, mr.created_at DESC
            LIMIT :limit OFFSET :offset
            """
            values["limit"] = page_size
            values["offset"] = offset
            records = await database.fetch_all(data_query, values=values)

            processed_records = []
            for record in records:
                record_dict = dict(record)
                source_data = record_dict.get("source_data")
                salesforce_results = record_dict.get("salesforce_results")
                object_types = []
                if salesforce_results:
                    try:
                        results = json.loads(salesforce_results) if isinstance(salesforce_results, str) else salesforce_results
                        if isinstance(results, dict):
                            object_types = [key for key in results.keys() if key != "error"]
                    except Exception as e:
                        print(f"Error parsing salesforce_results for failed record {record_dict['id']}: {e}")

                destination_type = record_dict.get("destination_type") or "salesforce"
                destination_name = MigrationRecordsService._extract_destination_name(
                    destination_type,
                    record_dict.get("mapping_config"),
                )

                processed_records.append({
                    "id": record_dict["id"],
                    "record_index": record_dict["record_index"],
                    "salesforce_object_types": ", ".join(object_types) if object_types else None,
                    "salesforce_results": salesforce_results,
                    "error_message": record_dict.get("error_message"),
                    "failed_at": (record_dict["processed_at"] or record_dict["created_at"]).isoformat() if record_dict.get("processed_at") or record_dict.get("created_at") else None,
                    "source_data": source_data,
                    "failed_status": "Failed",
                    "status": "failed",
                    "destination_type": destination_type,
                    "destination_name": destination_name,
                })

            return {
                "records": processed_records,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
            }

        except Exception as e:
            print(f"❌ Error in get_all_failed_migrations_paginated: {e}")
            raise

    @staticmethod
    async def get_migration_summary(user_id: str) -> Dict[str, Any]:
        """Get summary statistics for a user's migrations"""
        # Get batch-level statistics
        batch_query = """
        SELECT 
            COUNT(*) as total_batches,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed_batches,
            COUNT(CASE WHEN status = 'in_progress' THEN 1 END) as in_progress_batches,
            COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_batches,
            SUM(total_records) as total_records_processed,
            SUM(successful_records) as total_successful_records,
            SUM(failed_records) as total_failed_records
        FROM migration_batches 
        WHERE user_id = :user_id
        """
        
        batch_result = await database.fetch_one(batch_query, values={"user_id": user_id})
        batch_stats = dict(batch_result) if batch_result else {}
        
        # Get record-level statistics for more accurate counts
        record_query = """
        SELECT 
            COUNT(*) as total_records,
            COUNT(CASE WHEN status = 'success' THEN 1 END) as successful_records,
            COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed_records,
            COUNT(CASE WHEN status = 'partial_success' THEN 1 END) as partial_success_records
        FROM migration_records 
        WHERE user_id = :user_id
        """
        
        record_result = await database.fetch_one(record_query, values={"user_id": user_id})
        record_stats = dict(record_result) if record_result else {}
        
        # Calculate success rate
        total_records = record_stats.get('total_records', 0)
        successful_records = record_stats.get('successful_records', 0)
        success_rate = 0
        
        if total_records > 0:
            success_rate = round((successful_records / total_records) * 100, 1)
        
        # Debug logging to see what's in the database

        print(f"   • Total records: {total_records}")
        print(f"   • Successful records: {successful_records}")
        print(f"   • Failed records: {record_stats.get('failed_records', 0)}")
        print(f"   • Partial success records: {record_stats.get('partial_success_records', 0)}")
        print(f"   • Calculated success rate: {success_rate}%")
        
        # Also check what statuses exist in the database
        status_query = """
        SELECT status, COUNT(*) as count
        FROM migration_records 
        WHERE user_id = :user_id
        GROUP BY status
        """
        status_result = await database.fetch_all(status_query, values={"user_id": user_id})
        if status_result:
            print(f"   • Status breakdown:")
            for row in status_result:
                print(f"     - {row['status']}: {row['count']}")
        else:
            print(f"   • No status records found")
        
        # Check if there are any records at all for this user
        all_records_query = """
        SELECT COUNT(*) as total_count
        FROM migration_records 
        WHERE user_id = :user_id
        """
        all_records_result = await database.fetch_one(all_records_query, values={"user_id": user_id})
        if all_records_result:
            print(f"   • Total migration records in table: {all_records_result['total_count']}")
        else:
            print(f"   • No migration records found in table")
        
        # Get reusable flow count
        flow_query = """
        SELECT COUNT(*) as total_flows
        FROM migration_flows 
        WHERE user_id = :user_id
        """
        
        flow_result = await database.fetch_one(flow_query, values={"user_id": user_id})
        flow_stats = dict(flow_result) if flow_result else {}
        
        # Combine and format the statistics
        summary = {
            "total_migrations": batch_stats.get('total_batches', 0),
            "success_rate": success_rate,
            "total_records": total_records,
            "failed_records": record_stats.get('failed_records', 0),
            "reusable_flows": flow_stats.get('total_flows', 0),
            "completed_batches": batch_stats.get('completed_batches', 0),
            "in_progress_batches": batch_stats.get('in_progress_batches', 0),
            "failed_batches": batch_stats.get('failed_batches', 0),
            "total_records_processed": batch_stats.get('total_records_processed', 0),
            "total_successful_records": record_stats.get('successful_records', 0),
            "partial_success_records": record_stats.get('partial_success_records', 0)
        }
        
        return summary

    @staticmethod
    async def get_migration_records(batch_id: str) -> List[Dict[str, Any]]:
        """Get all migration records for a specific batch"""
        try:
            query = """
            SELECT 
                id,
                batch_id,
                user_id,
                record_index,
                source_data,
                mapped_data,
                salesforce_results,
                status,
                error_message,
                created_at,
                processed_at
            FROM migration_records 
            WHERE batch_id = :batch_id
            ORDER BY record_index ASC
            """
            
            records = await database.fetch_all(query, values={"batch_id": batch_id})
            
            # Convert database records to dictionaries
            migration_records = []
            for record in records:
                record_dict = dict(record)
                
                # Parse JSON fields if they exist
                if record_dict.get('source_data'):
                    try:
                        record_dict['source_data'] = json.loads(record_dict['source_data']) if isinstance(record_dict['source_data'], str) else record_dict['source_data']
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                if record_dict.get('mapped_data'):
                    try:
                        record_dict['mapped_data'] = json.loads(record_dict['mapped_data']) if isinstance(record_dict['mapped_data'], str) else record_dict['mapped_data']
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                if record_dict.get('salesforce_results'):
                    try:
                        record_dict['salesforce_results'] = json.loads(record_dict['salesforce_results']) if isinstance(record_dict['salesforce_results'], str) else record_dict['salesforce_results']
                    except (json.JSONDecodeError, TypeError):
                        pass
                
                # Convert datetime objects to ISO format strings
                if record_dict.get('created_at'):
                    record_dict['created_at'] = record_dict['created_at'].isoformat()
                if record_dict.get('processed_at'):
                    record_dict['processed_at'] = record_dict['processed_at'].isoformat()
                
                migration_records.append(record_dict)
            
            return migration_records
            
        except Exception as e:
            print(f"❌ Error getting migration records for batch {batch_id}: {e}")
            return []

    # Status filter values for export: success vs failed
    EXPORT_STATUS_SUCCESS = ("success", "created", "updated")
    EXPORT_STATUS_FAILED = ("failed", "error", "create_failed", "update_failed")

    @staticmethod
    async def count_migration_records_batch(
        batch_id: str,
        status_filter: Optional[str] = None,
    ) -> int:
        """Count migration records for a batch (for export sizing)."""
        if status_filter == "success":
            clause = "AND status IN ('success', 'created', 'updated')"
        elif status_filter == "failed":
            clause = "AND status IN ('failed', 'error', 'create_failed', 'update_failed')"
        else:
            clause = ""
        row = await database.fetch_one(
            f"SELECT COUNT(*) as c FROM migration_records WHERE batch_id = :batch_id {clause}",
            values={"batch_id": batch_id},
        )
        return row["c"] if row else 0

    @staticmethod
    async def count_migration_records_user_scoped(
        user_id: str,
        status_filter: Optional[str] = None,
    ) -> int:
        """Count migration records for a user (for export sizing)."""
        if status_filter == "success":
            clause = "AND mr.status IN ('success', 'created', 'updated')"
        elif status_filter == "failed":
            clause = "AND mr.status IN ('failed', 'error', 'create_failed', 'update_failed')"
        else:
            clause = ""
        row = await database.fetch_one(
            f"SELECT COUNT(*) as c FROM migration_records mr WHERE mr.user_id = :user_id {clause}",
            values={"user_id": user_id},
        )
        return row["c"] if row else 0

    @staticmethod
    async def iterate_migration_records(
        batch_id: str,
        batch_size: int = 500,
        status_filter: Optional[str] = None,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """
        Yield batches of migration records for scalable rollback and processing.
        Use this instead of get_migration_records for large batches to avoid OOM.
        status_filter: 'success' | 'failed' | None (all)
        """
        try:
            offset = 0
            while True:
                if status_filter == "success":
                    query = """
                    SELECT mr.id, mr.batch_id, mr.user_id, mr.record_index, mr.source_data, mr.mapped_data,
                           mr.salesforce_results, mr.status, mr.error_message, mr.created_at, mr.processed_at
                    FROM migration_records mr
                    WHERE mr.batch_id = :batch_id AND mr.status IN ('success', 'created', 'updated')
                    ORDER BY mr.record_index ASC
                    LIMIT :limit OFFSET :offset
                    """
                elif status_filter == "failed":
                    query = """
                    SELECT mr.id, mr.batch_id, mr.user_id, mr.record_index, mr.source_data, mr.mapped_data,
                           mr.salesforce_results, mr.status, mr.error_message, mr.created_at, mr.processed_at
                    FROM migration_records mr
                    WHERE mr.batch_id = :batch_id AND mr.status IN ('failed', 'error', 'create_failed', 'update_failed')
                    ORDER BY mr.record_index ASC
                    LIMIT :limit OFFSET :offset
                    """
                else:
                    query = """
                    SELECT id, batch_id, user_id, record_index, source_data, mapped_data,
                           salesforce_results, status, error_message, created_at, processed_at
                    FROM migration_records
                    WHERE batch_id = :batch_id
                    ORDER BY record_index ASC
                    LIMIT :limit OFFSET :offset
                    """
                rows = await database.fetch_all(
                    query,
                    values={"batch_id": batch_id, "limit": batch_size, "offset": offset},
                )
                if not rows:
                    break

                batch = []
                for record in rows:
                    record_dict = dict(record)
                    for json_field in ("source_data", "mapped_data", "salesforce_results"):
                        val = record_dict.get(json_field)
                        if val:
                            try:
                                record_dict[json_field] = (
                                    json.loads(val) if isinstance(val, str) else val
                                )
                            except (json.JSONDecodeError, TypeError):
                                pass
                    for dt_field in ("created_at", "processed_at"):
                        if record_dict.get(dt_field):
                            record_dict[dt_field] = record_dict[dt_field].isoformat()
                    batch.append(record_dict)

                yield batch
                offset += len(batch)
                if len(batch) < batch_size:
                    break
        except Exception as e:
            print(f"❌ Error iterating migration records for batch {batch_id}: {e}")

    @staticmethod
    async def iterate_migration_records_user_scoped(
        user_id: str,
        batch_size: int = 500,
        status_filter: Optional[str] = None,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """
        Yield batches of migration records for a user (all batches), for export.
        Includes per-record source/destination metadata from migration_batches.
        status_filter: 'success' | 'failed' | 'all' | None
        """
        try:
            offset = 0
            while True:
                if status_filter == "success":
                    status_clause = "AND mr.status IN ('success', 'created', 'updated')"
                elif status_filter == "failed":
                    status_clause = "AND mr.status IN ('failed', 'error', 'create_failed', 'update_failed')"
                else:
                    status_clause = ""

                query = f"""
                SELECT mr.id, mr.batch_id, mr.user_id, mr.record_index, mr.source_data, mr.mapped_data,
                       mr.salesforce_results, mr.status, mr.error_message, mr.created_at, mr.processed_at,
                       mb.destination_type, mb.mapping_config
                FROM migration_records mr
                LEFT JOIN migration_batches mb ON mr.batch_id = mb.id
                WHERE mr.user_id = :user_id {status_clause}
                ORDER BY mr.processed_at DESC NULLS LAST, mr.created_at DESC
                LIMIT :limit OFFSET :offset
                """
                rows = await database.fetch_all(
                    query,
                    values={"user_id": user_id, "limit": batch_size, "offset": offset},
                )
                if not rows:
                    break

                batch = []
                for record in rows:
                    record_dict = dict(record)
                    for json_field in ("source_data", "mapped_data", "salesforce_results"):
                        val = record_dict.get(json_field)
                        if val:
                            try:
                                record_dict[json_field] = (
                                    json.loads(val) if isinstance(val, str) else val
                                )
                            except (json.JSONDecodeError, TypeError):
                                pass
                    for dt_field in ("created_at", "processed_at"):
                        if record_dict.get(dt_field):
                            record_dict[dt_field] = record_dict[dt_field].isoformat()

                    # Per-record source/destination from batch mapping_config
                    mapping_config = record_dict.get("mapping_config")
                    if isinstance(mapping_config, str):
                        try:
                            mapping_config = json.loads(mapping_config) if mapping_config else {}
                        except Exception:
                            mapping_config = {}
                    elif mapping_config is None:
                        mapping_config = {}
                    dest_type = (record_dict.get("destination_type") or "salesforce").lower()
                    record_dict["_export_source_type"] = mapping_config.get("source_type") or ""
                    record_dict["_export_source_name"] = (
                        mapping_config.get("source_name")
                        or mapping_config.get("document_type")
                        or mapping_config.get("table_name")
                        or mapping_config.get("spreadsheet_name")
                        or ""
                    )
                    record_dict["_export_destination_type"] = dest_type
                    record_dict["_export_destination_name"] = (
                        mapping_config.get("destination_name")
                        or MigrationRecordsService._extract_destination_name(dest_type, mapping_config)
                        or dest_type.replace("_", " ").title()
                    )
                    batch.append(record_dict)

                yield batch
                offset += len(batch)
                if len(batch) < batch_size:
                    break
        except Exception as e:
            print(f"❌ Error iterating user-scoped migration records for user {user_id}: {e}")

    @staticmethod
    async def is_migration_batch_detail_expired(batch_id: str, user_id: str) -> bool:
        """True when retention has cleared row-level migration_records for this batch."""
        row = await database.fetch_one(
            """
            SELECT detail_purged_at, user_id FROM migration_batches WHERE id = :batch_id LIMIT 1
            """,
            values={"batch_id": batch_id},
        )
        if not row:
            return False
        rd = dict(row)
        if str(rd.get("user_id")) != str(user_id):
            return False
        return rd.get("detail_purged_at") is not None

    @staticmethod
    async def get_migration_records_paginated(
        batch_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Get migration records with pagination. Verifies user ownership via migration_batches.
        Returns metadata + paginated records, or None if batch not found/unauthorized.
        """
        try:
            # Verify ownership and get batch metadata
            batch_query = """
            SELECT id, user_id, batch_name, document_type, batch_type, total_records,
                   status, mapping_config, destination_type, detail_purged_at
            FROM migration_batches
            WHERE id = :batch_id
            LIMIT 1
            """
            batch_row = await database.fetch_one(
                batch_query, values={"batch_id": batch_id}
            )
            if not batch_row:
                return None
            batch_dict = dict(batch_row)
            if str(batch_dict.get("user_id")) != str(user_id):
                return None

            total_count = batch_dict.get("total_records") or 0
            sync_outcome = await MigrationRecordsService._get_sync_outcome_for_batch(batch_id, user_id)
            detail_purged_at = batch_dict.get("detail_purged_at")
            if detail_purged_at is not None:
                mapping_config = batch_dict.get("mapping_config")
                if isinstance(mapping_config, str):
                    try:
                        mapping_config = json.loads(mapping_config) if mapping_config else {}
                    except Exception:
                        mapping_config = {}
                elif mapping_config is None:
                    mapping_config = {}
                dest_type = (batch_dict.get("destination_type") or "salesforce").lower()
                dest_name = MigrationRecordsService._extract_destination_name(dest_type, mapping_config)
                view_ctx = await MigrationRecordsService.resolve_destination_view_context(
                    str(user_id), dest_type, mapping_config
                )
                purged_iso = (
                    detail_purged_at.isoformat()
                    if hasattr(detail_purged_at, "isoformat")
                    else str(detail_purged_at)
                )
                return {
                    "detail_expired": True,
                    "metadata": {
                        "batch_id": batch_id,
                        "batch_name": batch_dict.get("batch_name"),
                        "document_type": batch_dict.get("document_type"),
                        "batch_type": batch_dict.get("batch_type") or "data",
                        "total_records": total_count,
                        "status": batch_dict.get("status"),
                        "sync_outcome": sync_outcome,
                        "mapping_config": mapping_config,
                        "destination_type": dest_type,
                        "destination_name": dest_name,
                        "instance_url": view_ctx.get("instance_url"),
                        "spreadsheet_id": view_ctx.get("spreadsheet_id"),
                        "detail_purged_at": purged_iso,
                        "detail_available": False,
                        "detail_expired": True,
                    },
                    "records": [],
                    "total_count": total_count,
                    "page": max(1, page),
                    "page_size": min(max(1, page_size), 1000),
                }
            page = max(1, page)
            page_size = min(max(1, page_size), 1000)
            offset = (page - 1) * page_size
            search_pattern = f"%{search.strip()}%" if search and str(search).strip() else None
            if search_pattern:
                count_row = await database.fetch_one(
                    """
                    SELECT COUNT(*) AS c
                    FROM migration_records
                    WHERE batch_id = :batch_id
                      AND (
                        source_data::text ILIKE :search_pattern
                        OR mapped_data::text ILIKE :search_pattern
                        OR salesforce_results::text ILIKE :search_pattern
                        OR COALESCE(error_message, '') ILIKE :search_pattern
                        OR COALESCE(status, '') ILIKE :search_pattern
                      )
                    """,
                    values={"batch_id": batch_id, "search_pattern": search_pattern},
                )
                total_count = int(count_row["c"]) if count_row and count_row["c"] is not None else 0

            # Fetch paginated records
            recs_query = """
            SELECT id, batch_id, user_id, record_index, source_data, mapped_data,
                   salesforce_results, status, error_message, created_at, processed_at
            FROM migration_records
            WHERE batch_id = :batch_id
            """
            query_values: Dict[str, Any] = {"batch_id": batch_id, "limit": page_size, "offset": offset}
            if search_pattern:
                recs_query += """
              AND (
                source_data::text ILIKE :search_pattern
                OR mapped_data::text ILIKE :search_pattern
                OR salesforce_results::text ILIKE :search_pattern
                OR COALESCE(error_message, '') ILIKE :search_pattern
                OR COALESCE(status, '') ILIKE :search_pattern
              )
            """
                query_values["search_pattern"] = search_pattern
            recs_query += """
            ORDER BY record_index ASC
            LIMIT :limit OFFSET :offset
            """
            rows = await database.fetch_all(recs_query, values=query_values)

            records = []
            for record in rows:
                record_dict = dict(record)
                for json_field in ("source_data", "mapped_data", "salesforce_results"):
                    val = record_dict.get(json_field)
                    if val:
                        try:
                            record_dict[json_field] = (
                                json.loads(val) if isinstance(val, str) else val
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass
                record_dict["source_data"] = MigrationRecordsService._sanitize_row_payload(
                    record_dict.get("source_data")
                )
                record_dict["mapped_data"] = MigrationRecordsService._sanitize_row_payload(
                    record_dict.get("mapped_data")
                )
                for dt_field in ("created_at", "processed_at"):
                    if record_dict.get(dt_field):
                        record_dict[dt_field] = record_dict[dt_field].isoformat()
                records.append(record_dict)

            mapping_config = batch_dict.get("mapping_config")
            if isinstance(mapping_config, str):
                try:
                    mapping_config = json.loads(mapping_config) if mapping_config else {}
                except Exception:
                    mapping_config = {}
            elif mapping_config is None:
                mapping_config = {}

            dest_type = (batch_dict.get("destination_type") or "salesforce").lower()
            dest_name = MigrationRecordsService._extract_destination_name(dest_type, mapping_config)
            view_ctx = await MigrationRecordsService.resolve_destination_view_context(
                str(user_id), dest_type, mapping_config
            )

            return {
                "search": search.strip() if search and str(search).strip() else None,
                "metadata": {
                    "batch_id": batch_id,
                    "batch_name": batch_dict.get("batch_name"),
                    "document_type": batch_dict.get("document_type"),
                    "batch_type": batch_dict.get("batch_type") or "data",
                    "total_records": total_count,
                    "status": batch_dict.get("status"),
                    "sync_outcome": sync_outcome,
                    "mapping_config": mapping_config,
                    "destination_type": dest_type,
                    "destination_name": dest_name,
                    "instance_url": view_ctx.get("instance_url"),
                    "spreadsheet_id": view_ctx.get("spreadsheet_id"),
                    "detail_purged_at": None,
                    "detail_available": True,
                    "detail_expired": False,
                },
                "records": records,
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "detail_expired": False,
            }
        except Exception as e:
            print(f"❌ Error getting paginated migration records for batch {batch_id}: {e}")
            return None
