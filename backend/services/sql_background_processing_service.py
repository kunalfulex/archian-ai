import asyncio
import json
import os
import time
from typing import Dict, List, Any, Optional
from datetime import datetime, date
from services.sql_connector_service import SQLConnectorFactory, SQLConnectorService
from utils.job_stores import sql_tasks
from services.sql_ai_categorization_service import SQLAICategorizationService
from services.extraction_results_service import ExtractionResultsService
from db.connection import database
from utils.email_utils import send_database_sync_complete_email
import uuid


def _sql_connector_display_name(connector_data: Optional[dict]) -> Optional[str]:
    if not connector_data or "connector" not in connector_data:
        return None
    connector = connector_data["connector"]
    if isinstance(connector, dict):
        return (connector.get("name") or "").strip() or None
    if hasattr(connector, "name"):
        return (connector.name or "").strip() or None
    return None


def _sql_connector_id_from_sources(
    connector_data: Optional[dict],
    task_data: Optional[dict] = None,
) -> Optional[str]:
    cid = (task_data or {}).get("connector_id")
    if cid:
        return str(cid)
    if not connector_data or "connector" not in connector_data:
        return None
    connector = connector_data["connector"]
    if isinstance(connector, dict):
        cid = connector.get("id")
    elif hasattr(connector, "id"):
        cid = connector.id
    elif hasattr(connector, "get"):
        cid = connector.get("id")
    return str(cid) if cid else None


def _sql_save_source_name(
    connector_data: Optional[dict],
    *,
    table_name: Optional[str] = None,
) -> Optional[str]:
    """Connection display name for DB row (not table name) — mirrors Salesforce org name."""
    return _sql_connector_display_name(connector_data) or table_name


def _sql_persisted_extraction_metadata(
    task_data: dict,
    *,
    table_name: Optional[str] = None,
    table_names: Optional[List[str]] = None,
    connector_id: Optional[str] = None,
    connector_name: Optional[str] = None,
    sql_query: Optional[str] = None,
) -> Dict[str, Any]:
    """Build extraction_metadata for DB + continuation (mirrors SF _sf_persisted_extraction_metadata)."""
    from services.intent.extraction_metadata import build_persisted_extraction_metadata

    tq = task_data.get("table_and_query") if isinstance(task_data.get("table_and_query"), dict) else {}
    tq_meta = dict(tq.get("extraction_metadata") or {}) if isinstance(tq.get("extraction_metadata"), dict) else {}

    tables = [str(t).strip() for t in (table_names or tq.get("table_names") or []) if str(t).strip()]
    if not tables and table_name:
        tables = [table_name.strip()]
    elif not tables and tq.get("table_name"):
        tables = [str(tq["table_name"]).strip()]
    primary_table = (table_name or (tables[0] if tables else "") or "").strip()
    source_entity = (task_data.get("source_entity_key") or primary_table or "").strip()

    field_hints = list(tq_meta.get("selected_columns") or [])
    if not field_hints and isinstance(tq_meta.get("extract_contract"), dict):
        field_hints = list(tq_meta["extract_contract"].get("allowed_select_columns") or [])

    extra: Dict[str, Any] = {
        "source_type": "sql",
        "table_name": primary_table or None,
        "table_names": tables or None,
        "sql_query": sql_query or tq.get("query") or tq.get("sql_query"),
    }
    if connector_name:
        extra["source_name"] = connector_name
    if connector_id:
        extra["source_connector_id"] = str(connector_id)
    rules = task_data.get("extraction_rules")
    if isinstance(rules, dict) and rules:
        extra["extraction_rules"] = rules
    extra.update(tq_meta)

    return build_persisted_extraction_metadata(
        migration_intent=task_data.get("migration_intent"),
        extract_field_scope=task_data.get("extract_field_scope"),
        related_entity_keys=task_data.get("related_entity_keys"),
        source_entity_key=source_entity or None,
        field_hints=field_hints or None,
        destination=task_data.get("destination"),
        destination_name=task_data.get("destination_name"),
        extra=extra,
    )


def _human_readable_analysis_error(exc: Exception) -> str:
    """Convert technical exception to a short, user-facing message for email and UI."""
    msg = str(exc).strip() or type(exc).__name__
    # Map known technical errors to friendly text
    if "object has no attribute 'get'" in msg or "AttributeError" in msg:
        return "A configuration error occurred. Please try re-saving your database connection settings."
    if "connection" in msg.lower() or "connect" in msg.lower() or "timeout" in msg.lower():
        return f"Could not connect to the database. Please check host, port, and firewall. Details: {msg[:200]}"
    if "login" in msg.lower() or "authentication" in msg.lower() or "password" in msg.lower():
        return "Login failed. Please check your username and password."
    if "permission" in msg.lower() or "access denied" in msg.lower():
        return "Access denied. Please check database user permissions."
    # Keep message short for UI; cap length
    if len(msg) > 280:
        return msg[:277] + "..."
    return msg


class SQLBackgroundProcessingService:
    """Service for background processing of SQL database analysis"""
    
    def __init__(self):
        self.ai_service = SQLAICategorizationService()
        self.connector_service = SQLConnectorService()
    
    async def process_database_analysis(self, connector_id: str, user_id: str, user_email: str):
        """
        Background job to analyze entire database and categorize all tables
        """
        try:
            # Starting database analysis for connector
            # Reset analysis_completed flag at the beginning of a new run
            try:
                await database.execute(
                    """
                    UPDATE sql_connectors
                    SET analysis_completed = FALSE
                    WHERE id = :connector_id AND user_id = :user_id
                    """,
                    {"connector_id": connector_id, "user_id": user_id},
                )
            except Exception as reset_err:
                # Don't fail the whole job if we can't reset the flag,
                # but log it for debugging.
                print(f"⚠️ Failed to reset analysis_completed flag for connector {connector_id}: {reset_err}")

            # Get connector details
            connector = await self.get_connector_details(connector_id, user_id)
            if not connector:
                print(f"❌ Connector {connector_id} not found")
                return

            try:
                from services.labeling.readiness_service import on_metadata_sync_started

                await on_metadata_sync_started(
                    user_id=str(user_id),
                    connector_type="sql",
                    connector_ref={"connector_id": connector_id},
                )
            except Exception as readiness_start_err:
                print(f"⚠️ [SQL Analysis] Readiness sync-start hook failed: {readiness_start_err}")

            # Previous schema (for delta billing: charge only for new tables/columns on re-analysis)
            prev_table_columns: Dict[str, int] = {}
            try:
                rows = await database.fetch_all(
                    """
                    SELECT table_name, schema_data FROM sql_extracted_schemas
                    WHERE connector_id = :connector_id
                    """,
                    values={"connector_id": connector_id},
                )
                for row in rows or []:
                    name = (row.get("table_name") if isinstance(row, dict) else getattr(row, "table_name", None)) or ""
                    schema = row.get("schema_data") if isinstance(row, dict) else getattr(row, "schema_data", None)
                    if isinstance(schema, str):
                        try:
                            schema = json.loads(schema) if schema else {}
                        except Exception:
                            schema = {}
                    cols = schema.get("columns", []) if isinstance(schema, dict) else []
                    prev_table_columns[name] = len(cols) if cols else 0
            except Exception as e:
                print(f"⚠️ [SQL Analysis] Could not load previous schema for delta: {e}")

            # Schema analysis is included; no credit check

            connector_type = (connector.get('connector_type') or '').strip().lower()
            db_name = connector.get('database_name') or ''
            print(f"[SQL Analysis] connector_id={connector_id} connector_type={connector_type} database_name={db_name!r}")
            
            # Create connector instance
            try:
                connector_config = self.prepare_connector_config(connector)
                # Redact password for debug
                config_debug = {k: ('***' if k == 'password' else v) for k, v in connector_config.items()}
                print(f"[SQL Analysis] connector_config (redacted)={config_debug}")
                
                connector_instance = SQLConnectorFactory.create_connector(
                    connector['connector_type'], 
                    connector_config
                )
            except Exception as e:
                print(f"❌ Error preparing connector config: {e}")
                import traceback
                traceback.print_exc()
                raise e
            
            # Get all tables
            try:
                tables = await connector_instance.get_tables()
                print(f"[SQL Analysis] get_tables() returned {len(tables)} tables: {tables[:20]}{'...' if len(tables) > 20 else ''}")
            except Exception as e:
                print(f"❌ [SQL Analysis] get_tables() failed: {e}")
                import traceback
                traceback.print_exc()
                tables = []
            
            # Process each table
            processed_tables = 0
            total_tables = len(tables)
            total_columns = 0
            current_table_columns: Dict[str, int] = {}  # for delta billing
            if total_tables == 0:
                print(f"[SQL Analysis] WARNING: 0 tables returned from get_tables() for connector_type={connector_type} database_name={db_name!r}. Check connection database and permissions.")
            elif connector_id:
                try:
                    from services.labeling.readiness_service import report_metadata_sync_progress

                    await report_metadata_sync_progress(
                        user_id=str(user_id),
                        connector_type="sql",
                        connector_ref={"connector_id": connector_id},
                        processed=0,
                        total=total_tables,
                    )
                except Exception as progress_init_err:
                    print(f"⚠️ [SQL Analysis] Progress init failed: {progress_init_err}")

            import time

            last_progress_flush = 0.0

            async def _flush_sql_progress(processed: int, *, force: bool = False) -> None:
                nonlocal last_progress_flush
                if not connector_id or total_tables <= 0:
                    return
                now = time.monotonic()
                if not force and now - last_progress_flush < 1.0 and processed % 5 != 0:
                    return
                last_progress_flush = now
                try:
                    from services.labeling.readiness_service import report_metadata_sync_progress

                    await report_metadata_sync_progress(
                        user_id=str(user_id),
                        connector_type="sql",
                        connector_ref={"connector_id": connector_id},
                        processed=processed,
                        total=total_tables,
                    )
                except Exception as progress_err:
                    print(f"⚠️ [SQL Analysis] Progress update failed: {progress_err}")
            
            # FIRST PASS: Insert all tables with NULL ai_categorization and count columns
            for idx, table_name in enumerate(tables):
                try:
                    # Get table schema
                    schema_data = await connector_instance.get_table_schema(table_name)
                    cols = schema_data.get("columns") if isinstance(schema_data, dict) else []
                    col_count = len(cols) if cols else 0
                    from services.connector_metadata.empty_catalog_cleanup import (
                        purge_sql_table_catalog_entry,
                        sql_schema_storable_column_count,
                    )

                    storable = sql_schema_storable_column_count(schema_data)
                    if storable == 0:
                        await purge_sql_table_catalog_entry(
                            user_id, connector_id, table_name
                        )
                        print(f"⛔ Removed SQL table {table_name} — no storable columns")
                        continue
                    total_columns += storable
                    current_table_columns[table_name] = storable
                    # Store table schema first (with NULL ai_categorization)
                    await self.store_table_schema_only(
                        user_id=user_id,
                        connector_id=connector_id,
                        table_name=table_name,
                        schema_data=schema_data
                    )
                except Exception as e:
                    print(f"❌ [SQL Analysis] Error storing schema for table {table_name}: {e}")
                    import traceback
                    traceback.print_exc()
                finally:
                    await _flush_sql_progress(idx + 1)

            if total_tables > 0:
                await _flush_sql_progress(total_tables, force=True)
            
            # SECOND PASS: queue background labeling (non-blocking; replaces inline LLM)
            from services.connector_metadata.empty_catalog_cleanup import (
                purge_sql_tables_without_columns,
            )
            from services.labeling.schedule import schedule_entity_labeling_fire_and_forget

            await purge_sql_tables_without_columns(user_id, connector_id)
            label_tables = [
                t
                for t in tables
                if current_table_columns.get(t, 0) > 0
            ]
            schedule_entity_labeling_fire_and_forget(
                user_id=user_id,
                connector_type="sql",
                connector_ref={"connector_id": connector_id},
                entity_keys=label_tables,
            )
            processed_tables = len(label_tables)
            
            # Close connection
            try:
                await connector_instance.close_connection()
            except Exception as e:
                print(f"Warning: Error closing connection: {e}")
            
            # Schema analysis is included; no credit deduction (log delta for debugging)
            prev_tables = set(prev_table_columns.keys())
            current_tables = set(current_table_columns.keys())
            new_tables = current_tables - prev_tables
            new_entities = len(new_tables)
            new_fields = sum(current_table_columns[t] for t in new_tables) + sum(
                max(0, current_table_columns[t] - prev_table_columns.get(t, 0))
                for t in (current_tables & prev_tables)
            )
            if new_entities > 0 or new_fields > 0:
                print(f"✅ [SQL Analysis] Schema delta: {new_entities} new tables, {new_fields} new columns (included)")
            else:
                print(f"✅ [SQL Analysis] No schema delta")
            
            # Mark analysis as completed and clear any previous error
            await database.execute(
                """
                UPDATE sql_connectors
                SET analysis_completed = TRUE, analysis_error = NULL
                WHERE id = :connector_id AND user_id = :user_id
                """,
                {"connector_id": connector_id, "user_id": user_id},
            )

            try:
                from services.labeling.readiness_service import on_metadata_sync_complete

                await on_metadata_sync_complete(
                    user_id=str(user_id),
                    connector_type="sql",
                    connector_ref={"connector_id": str(connector_id)},
                )
            except Exception as readiness_err:
                print(f"⚠️ [SQL Analysis] Readiness hook failed: {readiness_err}")
            
        except Exception as e:
            print(f"❌ Database analysis failed: {e}")
            import traceback
            traceback.print_exc()
            human_message = _human_readable_analysis_error(e)
            try:
                await database.execute(
                    """
                    UPDATE sql_connectors
                    SET analysis_error = :analysis_error
                    WHERE id = :connector_id AND user_id = :user_id
                    """,
                    {
                        "connector_id": connector_id,
                        "user_id": user_id,
                        "analysis_error": human_message,
                    },
                )
            except Exception as update_err:
                print(f"⚠️ Failed to store analysis_error for connector {connector_id}: {update_err}")
            # Send error email with human-readable message
            try:
                connector_for_name = await self.get_connector_details(connector_id, user_id)
                connector_name = (connector_for_name or {}).get("name") or "Your database"
                await send_database_sync_complete_email(
                    user_email,
                    connector_name,
                    0,
                    error=human_message,
                )
            except Exception as email_error:
                print(f"Failed to send error email: {email_error}")
            try:
                from services.labeling.store import LabelingStore

                await LabelingStore.clear_metadata_sync_in_progress(
                    user_id=str(user_id),
                    connector_type="sql",
                    connector_instance_id=str(connector_id),
                )
            except Exception as clear_err:
                print(f"⚠️ Failed to clear SQL sync-in-progress flag: {clear_err}")
    
    async def get_connector_details(self, connector_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get connector details from database"""
        try:
            result = await database.fetch_one(
                """
                SELECT * FROM sql_connectors 
                WHERE id = :connector_id AND user_id = :user_id
                """,
                {"connector_id": connector_id, "user_id": user_id}
            )
            return dict(result) if result else None
        except Exception as e:
            print(f"Error getting connector details: {e}")
            return None
    
    @staticmethod
    def _parse_additional_config(raw) -> Dict[str, Any]:
        from services.sql_connector_service import parse_connector_additional_config

        return parse_connector_additional_config(raw)

    def prepare_connector_config(self, connector: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare connector configuration with decrypted password"""
        config = {
            'connector_type': connector.get('connector_type'),
            'host': connector.get('host'),
            'port': connector.get('port'),
            'database_name': connector.get('database_name'),
            'username': connector.get('username'),
            'ssl_enabled': connector.get('ssl_enabled', False),
            'additional_config': self._parse_additional_config(connector.get('additional_config')),
        }
        
        # Decrypt password if available
        if connector.get('password_encrypted'):
            try:
                config['password'] = self.connector_service.decrypt_password(
                    connector['password_encrypted']
                )
            except Exception as e:
                print(f"Warning: Could not decrypt password, using as-is: {e}")
                # If decryption fails, assume it's already plain text
                config['password'] = connector['password_encrypted']
        
        # Handle connection string for complex connections
        if connector.get('connection_string'):
            config['connection_string'] = connector['connection_string']
        
        # Handle BigQuery service account key
        if connector.get('connector_type') == 'bigquery' and connector.get('additional_config'):
            config['service_account_key'] = connector['additional_config'].get('service_account_key')
        
        return config
    
    async def get_sample_data(self, connector_instance, table_name: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Get sample data from table"""
        try:
            # Use database-appropriate syntax for row limit
            if hasattr(connector_instance, 'config') and connector_instance.config.get('connector_type') == 'oracle':
                query = f"SELECT * FROM {table_name} WHERE ROWNUM <= {limit}"
            elif hasattr(connector_instance, 'config') and connector_instance.config.get('connector_type', '').lower() in ('mssql', 'sqlserver'):
                # MSSQL uses TOP, not LIMIT; quote table name for safety
                quoted = f"[{table_name}]" if table_name and not table_name.startswith('[') else table_name
                query = f"SELECT TOP {limit} * FROM {quoted}"
            else:
                query = f"SELECT * FROM {table_name} LIMIT {limit}"
            
            return await connector_instance.execute_query(query)
        except Exception as e:
            print(f"Error getting sample data from {table_name}: {e}")
            return []
    
    async def store_table_analysis(
        self, 
        user_id: str, 
        connector_id: str, 
        table_name: str, 
        schema_data: Dict[str, Any], 
        ai_categorization: Dict[str, Any]
    ):
        """Store table analysis results in database"""
        try:
            # Check if table already exists
            existing = await database.fetch_one(
                """
                SELECT id FROM sql_extracted_schemas 
                WHERE connector_id = :connector_id AND table_name = :table_name
                """,
                {"connector_id": connector_id, "table_name": table_name}
            )
            
            if existing:
                # Update existing record
                await database.execute(
                    """
                    UPDATE sql_extracted_schemas 
                    SET schema_data = :schema_data, 
                        ai_categorization = :ai_categorization,
                        extracted_at = NOW()
                    WHERE id = :id
                    """,
                    {
                        "id": existing["id"],
                        "schema_data": json.dumps(schema_data),
                        "ai_categorization": json.dumps(ai_categorization)
                    }
                )
            else:
                # Insert new record
                await database.execute(
                    """
                    INSERT INTO sql_extracted_schemas 
                    (id, user_id, connector_id, table_name, schema_data, ai_categorization, extracted_at)
                    VALUES (:id, :user_id, :connector_id, :table_name, :schema_data, :ai_categorization, NOW())
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user_id,
                        "connector_id": connector_id,
                        "table_name": table_name,
                        "schema_data": json.dumps(schema_data),
                        "ai_categorization": json.dumps(ai_categorization)
                    }
                )
        except Exception as e:
            print(f"Error storing table analysis: {e}")

    async def store_table_schema_only(
        self, 
        user_id: str, 
        connector_id: str, 
        table_name: str, 
        schema_data: Dict[str, Any]
    ):
        """Store table schema in database with NULL ai_categorization (to track total tables)"""
        try:
            # Check if table already exists
            existing = await database.fetch_one(
                """
                SELECT id FROM sql_extracted_schemas 
                WHERE connector_id = :connector_id AND table_name = :table_name
                """,
                {"connector_id": connector_id, "table_name": table_name}
            )
            
            if not existing:
                # Insert new record with NULL ai_categorization
                await database.execute(
                    """
                    INSERT INTO sql_extracted_schemas 
                    (id, user_id, connector_id, table_name, schema_data, ai_categorization, extracted_at)
                    VALUES (:id, :user_id, :connector_id, :table_name, :schema_data, NULL, NOW())
                    """,
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user_id,
                        "connector_id": connector_id,
                        "table_name": table_name,
                        "schema_data": json.dumps(schema_data)
                    }
                )
        except Exception as e:
            print(f"Error storing table schema: {e}")

    async def update_table_categorization(
        self, 
        user_id: str, 
        connector_id: str, 
        table_name: str, 
        ai_categorization: Dict[str, Any]
    ):
        """Update table with AI categorization result"""
        try:
            # Update existing record with categorization
            await database.execute(
                """
                UPDATE sql_extracted_schemas 
                SET ai_categorization = :ai_categorization,
                    extracted_at = NOW()
                WHERE connector_id = :connector_id 
                  AND table_name = :table_name
                  AND user_id = :user_id
                """,
                {
                    "connector_id": connector_id,
                    "table_name": table_name,
                    "user_id": user_id,
                    "ai_categorization": json.dumps(ai_categorization)
                }
            )
        except Exception as e:
            print(f"Error updating table categorization: {e}")


async def start_database_analysis(connector_id: str, user_id: str, user_email: str):
    """Start background database analysis job"""
    job_id = str(uuid.uuid4())
    
    # Start background task
    service = SQLBackgroundProcessingService()
    asyncio.create_task(service.process_database_analysis(connector_id, user_id, user_email))
    
    return job_id


# SQL Extraction Task Management (Redis-backed via sql_tasks)


def _sanitize_for_redis(obj: Any) -> Any:
    """Convert Record/datetime to JSON-serializable form for Redis storage."""
    if obj is None:
        return None
    if hasattr(obj, "_mapping"):  # databases Record
        return dict(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_redis(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_redis(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return obj


async def start_sql_extraction_task(connector_data: dict, table_and_query: dict, user_id: str, comprehensive_prompt: str, destination: str = "salesforce", destination_name: str = None, conversation_id: str = None, parent_job_id: str = None, extraction_rules: Optional[Dict[str, Any]] = None, extracted_data: Optional[List[Dict[str, Any]]] = None, existing_job_id: str = None, flow_execution_id: Optional[str] = None, extraction_profile: Optional[str] = None, template: Optional[str] = None, migration_intent: Optional[Dict[str, Any]] = None, extract_field_scope: Optional[Dict[str, Any]] = None, related_entity_keys: Optional[List[str]] = None, source_entity_key: Optional[str] = None) -> str:
    """Start a background SQL extraction job. If existing_job_id is provided, updates that Redis row and spawns (for early job_id emission)."""
    job_id = existing_job_id or str(uuid.uuid4())

    # Check if this is cleanup intent (extracted_data provided means we should use existing data)
    intent = None
    cleanup_extraction_rules = None
    if extracted_data and parent_job_id:
        # If extracted_data is provided, this is likely a cleanup intent
        intent = "cleanup"
        cleanup_extraction_rules = extraction_rules

    # If using existing task, check for cancellation before spawning
    if existing_job_id:
        existing = await sql_tasks.get(job_id)
        if existing and existing.get("cancelled"):
            print(f"🛑 SQL extraction job {job_id} cancelled by user before spawn")
            await sql_tasks.update(job_id, {"status": "cancelled"})
            return job_id

    # Store job data in Redis (sql_tasks store; keyed by job_id)
    task_data = {
        "job_id": job_id,
        "user_id": user_id,
        "connector_data": connector_data,
        "table_and_query": table_and_query,
        "comprehensive_prompt": comprehensive_prompt,
        "destination": destination,
        "destination_name": destination_name,
        "conversation_id": conversation_id,
        "parent_job_id": parent_job_id,
        "extraction_rules": extraction_rules,  # Store saved extraction rules
        "extracted_data": extracted_data,  # Store processed data from extraction_results table
        "extraction_profile": extraction_profile,
        "template": template,
        "migration_intent": migration_intent,
        "extract_field_scope": extract_field_scope,
        "related_entity_keys": related_entity_keys,
        "source_entity_key": source_entity_key,
        "intent": intent,  # Set intent if cleanup (extracted_data provided)
        "cleanup_extraction_rules": cleanup_extraction_rules,  # Store cleanup rules if cleanup intent
        "status": "processing",
        "progress": 0,
        "message": "Starting SQL extraction...",
        "result": None,
        "error": None,
        "created_at": datetime.now().isoformat()
    }
    if flow_execution_id:
        task_data["flow_execution_id"] = flow_execution_id
    await sql_tasks.set(job_id, _sanitize_for_redis(task_data))

    # Start background task
    asyncio.create_task(_process_sql_extraction(job_id))

    return job_id

async def _process_sql_extraction(task_id: str):
    """Process SQL extraction in background"""
    try:
        task_data = await sql_tasks.get(task_id)
        if not task_data:
            print(f"❌ Task {task_id} not found")
            return
        if task_data.get("cancelled"):
            print(f"🛑 SQL extraction task {task_id} cancelled by user")
            await sql_tasks.update(task_id, {"status": "cancelled"})
            return

        # Update status
        await _update_task_status(task_id, "processing", 10, "Connecting to database...")
        
        # Get connector data
        connector_data = task_data["connector_data"]
        table_and_query = task_data["table_and_query"]
        user_id = str(task_data["user_id"])  # Convert UUID to string
        comprehensive_prompt = task_data["comprehensive_prompt"]
        destination = task_data.get("destination", "salesforce")
        destination_name = task_data.get("destination_name")
        conversation_id = task_data.get("conversation_id")
        extraction_rules = task_data.get("extraction_rules")  # Get saved extraction rules
        
        # Check if this is cleanup intent with existing extracted data (passed as parameter)
        cleanup_extracted_data = task_data.get("extracted_data")
        is_cleanup_flow = task_data.get("intent") == "cleanup" and cleanup_extracted_data
        
        if is_cleanup_flow:
            # Cleanup flow: Accumulate cleanup rules with existing extraction rules
            # This preserves all rules from previous operations and adds new cleanup rules
            await _update_task_status(task_id, "processing", 10, "Processing cleanup data...")
            
            flat_data = cleanup_extracted_data
            
            # Get existing cleanup rules from task metadata (from quality report)
            cleanup_extraction_rules = task_data.get("cleanup_extraction_rules")
            quality_report_rules = cleanup_extraction_rules or {}
            if not quality_report_rules:
                # Fallback to extraction_rules if cleanup_extraction_rules not set
                quality_report_rules = task_data.get("extraction_rules", {})
            
            # Get destination override flag (needed for metadata update later)
            destination_was_overridden = task_data.get("destination_was_overridden", False)
            
            await _update_task_status(task_id, "processing", 20, "Ready for unified processing pipeline...")
            
            # Extract table_name from table_and_query for extract_structured_data_from_sql
            table_name = None
            selected_table_names = []
            if isinstance(table_and_query, dict):
                if table_and_query.get("table_names") and isinstance(table_and_query["table_names"], list):
                    selected_table_names = table_and_query["table_names"]
                elif table_and_query.get("table_name"):
                    selected_table_names = [table_and_query["table_name"]]
            table_name = selected_table_names[0] if selected_table_names else None
            query = table_and_query.get("query") or table_and_query.get("sql_query") if isinstance(table_and_query, dict) else None
            
            # Extract connector_id for formatted_result - prioritize from task_data (from message metadata), then from connector_data
            connector_id = task_data.get("connector_id")
            if not connector_id and connector_data and "connector" in connector_data:
                connector = connector_data["connector"]
                if hasattr(connector, 'get'):
                    connector_id = connector.get('id')
                elif hasattr(connector, 'id'):
                    connector_id = connector.id
                elif isinstance(connector, dict):
                    connector_id = connector.get('id')
            
            # Route through extract_structured_data_from_sql
            # For cleanup flow: Don't pass saved_extraction_rules - let extract_structured_data_from_sql generate rules from cleanup prompt
            # Quality report rules will be merged afterward
            task_data = await sql_tasks.get(task_id)
            if task_data and task_data.get("cancelled"):
                print(f"🛑 SQL extraction task {task_id} cancelled by user")
                await sql_tasks.update(task_id, {"status": "cancelled"})
                return

            await _update_task_status(task_id, "processing", 60, "Processing data with AI...")

            try:
                from handlers.sql_handler import extract_structured_data_from_sql
                # Don't pass saved_extraction_rules for cleanup flow - let it generate new rules from cleanup prompt
                extraction_result = await extract_structured_data_from_sql(
                    flat_data,
                    user_id,
                    comprehensive_prompt,
                    table_name or "cleaned_data",
                    destination=destination,
                    destination_name=destination_name,
                    saved_extraction_rules=None,
                    extraction_profile=task_data.get("extraction_profile"),
                    intent=task_data.get("intent"),
                    template=task_data.get("template"),
                )
                
                # For cleanup flow: Merge quality report rules with prompt-generated rules
                # This matches the Google Sheets flow - merge rules AFTER OpenAI call
                extraction_rules = None
                if is_cleanup_flow and extraction_result and isinstance(extraction_result, (list, tuple)) and len(extraction_result) >= 10:
                    extracted_data_from_ai, document_type_ai, document_description_ai, plan_limit_info_ai, original_record_count_ai, show_upgrade_option_ai, object_name_ai, updated_destination_name_ai, extraction_rules_result, mapping_context_summary_ai = extraction_result[:10]
                    
                    # Initialize extraction_rules_result from prompt-generated rules
                    extraction_rules_result_merged = extraction_rules_result if extraction_rules_result else {}
                    if not extraction_rules_result_merged:
                        extraction_rules_result_merged = {}
                    
                    # Merge quality report rules with prompt-generated rules (if quality_report_rules exists)
                    if quality_report_rules:
                        # Merge quality report rules with prompt-generated rules
                        # Prompt rules take precedence for filter_rules, deduplication_rules, transformation_rules
                        # Quality report data_cleanup_rules should be merged (not overwritten)
                        if quality_report_rules.get("data_cleanup_rules"):
                            existing_data_cleanup = extraction_rules_result_merged.get("data_cleanup_rules", {})
                            if isinstance(existing_data_cleanup, dict) and isinstance(quality_report_rules["data_cleanup_rules"], dict):
                                # Merge both dicts - prompt rules take precedence if there are conflicts
                                extraction_rules_result_merged["data_cleanup_rules"] = {**quality_report_rules["data_cleanup_rules"], **existing_data_cleanup}
                            else:
                                # If not both dicts, use quality report rules as fallback
                                if not existing_data_cleanup:
                                    extraction_rules_result_merged["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                        
                        # Use quality report rules for other rule types only if prompt didn't generate them
                        if quality_report_rules.get("filter_rules") and not extraction_rules_result_merged.get("filter_rules"):
                            extraction_rules_result_merged["filter_rules"] = quality_report_rules["filter_rules"]
                        if quality_report_rules.get("deduplication_rules") and not extraction_rules_result_merged.get("deduplication_rules"):
                            extraction_rules_result_merged["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                        if quality_report_rules.get("transformation_rules") and not extraction_rules_result_merged.get("transformation_rules"):
                            extraction_rules_result_merged["transformation_rules"] = quality_report_rules["transformation_rules"]
                    
                    # Now accumulate with original_extraction_rules to preserve original transformation rules
                    # Get existing extraction rules - prioritize original_extraction_rules from task metadata
                    existing_extraction_rules = {}
                    source_schema = None
                    
                    # First, try to get original_extraction_rules from task metadata (from initial extraction)
                    original_extraction_rules_from_task = task_data.get("original_extraction_rules")
                    
                    # Defensive check: ensure original_extraction_rules_from_task is a dict (not a list, None, or other type)
                    if original_extraction_rules_from_task is not None and isinstance(original_extraction_rules_from_task, dict) and len(original_extraction_rules_from_task) > 0:
                        existing_extraction_rules = original_extraction_rules_from_task
                        # Additional defensive check: ensure existing_extraction_rules is still a dict and not empty
                        if not isinstance(existing_extraction_rules, dict) or not existing_extraction_rules:
                            print(f"⚠️ [SQL CLEANUP FLOW] existing_extraction_rules is not a valid dict (type: {type(existing_extraction_rules)}, empty: {not existing_extraction_rules if isinstance(existing_extraction_rules, dict) else 'N/A'}), resetting to empty dict")
                            existing_extraction_rules = {}
                        else:
                            transformation_rules = existing_extraction_rules.get('transformation_rules')
                            if transformation_rules is not None and not isinstance(transformation_rules, dict):
                                print(f"⚠️ [SQL CLEANUP FLOW] transformation_rules exists but is not a dict (type: {type(transformation_rules)}), will not preserve transformation rules")
                    elif original_extraction_rules_from_task is not None:
                        # original_extraction_rules exists but is not a dict (could be a list, empty dict, or other type)
                        print(f"⚠️ [SQL CLEANUP FLOW] original_extraction_rules found but is not a valid dict (type: {type(original_extraction_rules_from_task)}, value: {original_extraction_rules_from_task}), resetting to empty dict")
                        existing_extraction_rules = {}
                    else:
                        # original_extraction_rules is None or not present
                        existing_extraction_rules = {}
                    
                    # Try to get source_schema from task metadata (prioritize original source_schema, not current_source_schema)
                    # We need the ORIGINAL source schema (before transformations) for rule accumulation
                    if not source_schema:
                        source_schema = task_data.get("source_schema")  # Original source schema from parent task
                        if not source_schema:
                            # Fallback: try to get from parent task directly
                            parent_job_id = task_data.get("parent_job_id")
                            if parent_job_id:
                                try:
                                    parent_task = await sql_tasks.get(parent_job_id)
                                    if parent_task:
                                        source_schema = parent_task.get("source_schema")
                                except Exception as e:
                                    print(f"⚠️ [SQL CLEANUP FLOW] Error getting source_schema from parent task: {e}")
                        
                        # If still no source_schema, extract from flat_data (original data before transformations)
                        if not source_schema and flat_data:
                            from utils.rule_accumulation import extract_source_schema_from_data
                            source_schema = extract_source_schema_from_data(flat_data)
                    
                    # Accumulate merged rules with original transformation rules
                    if source_schema and existing_extraction_rules:
                        from utils.rule_accumulation import accumulate_cleanup_rules
                        try:
                            # Combine extraction_rules_result_merged (prompt + quality report) with existing_extraction_rules (original transformation rules)
                            # Use extraction_rules_result_merged as new_cleanup_rules (they already include quality report rules)
                            # Ensure existing_extraction_rules is a dict (will be normalized in accumulate_cleanup_rules)
                            if not isinstance(existing_extraction_rules, dict):
                                existing_extraction_rules = {}
                            extraction_rules = accumulate_cleanup_rules(
                                existing_rules=existing_extraction_rules,
                                new_cleanup_rules=extraction_rules_result_merged,
                                source_schema=source_schema
                            )
                        except Exception as acc_error:
                            print(f"⚠️ [SQL CLEANUP FLOW] Error accumulating rules: {acc_error}")
                            import traceback
                            traceback.print_exc()
                            # Fallback: use extraction_rules_result_merged (already merged)
                            extraction_rules = extraction_rules_result_merged
                    else:
                        # No original transformation rules - only apply data cleanup rules
                        # Filter out transformation rules from OpenAI response, keep only data_cleanup_rules
                        extraction_rules = {}
                        
                        # Only include data_cleanup_rules from quality report (not transformation rules from OpenAI)
                        if quality_report_rules and quality_report_rules.get("data_cleanup_rules"):
                            extraction_rules["data_cleanup_rules"] = quality_report_rules["data_cleanup_rules"]
                        
                        # Also include deduplication_rules if present in quality report
                        if quality_report_rules and quality_report_rules.get("deduplication_rules"):
                            extraction_rules["deduplication_rules"] = quality_report_rules["deduplication_rules"]
                        
                        # Do NOT include transformation_rules from OpenAI response when there are no original transformation rules
                        # The data structure is already correct (first_name, last_name, email), we only need to clean the values
                        if extraction_rules_result_merged.get("transformation_rules"):
                            print(f"⚠️ [SQL CLEANUP FLOW] Skipping OpenAI-generated transformation_rules - no original transformation rules to preserve")
                        
                        # If extraction_rules is empty, set to None to avoid issues
                        if not extraction_rules:
                            extraction_rules = None
                    
                    # Update extraction_result tuple with merged rules
                    extraction_result = list(extraction_result)
                    extraction_result[8] = extraction_rules  # Update extraction_rules in the tuple
                    extraction_result = tuple(extraction_result)
                    
                    # Handle destination override case - update metadata if destination was overridden
                    if destination_was_overridden:
                        if extraction_rules_result_merged.get("document_type"):
                            task_data["document_type"] = extraction_rules_result_merged["document_type"]
                        if extraction_rules_result_merged.get("document_description"):
                            task_data["document_description"] = extraction_rules_result_merged["document_description"]
                        if extraction_rules_result_merged.get("object_name"):
                            task_data["object_name"] = extraction_rules_result_merged["object_name"]
                    
                    # Apply merged rules to extracted_data_from_ai (the data returned from OpenAI)
                    # This matches Google Sheets flow - always apply if extraction_rules exists
                    if extraction_rules:
                        from utils.extraction_utils import apply_all_extraction_rules
                        extracted_data_from_ai = apply_all_extraction_rules(extracted_data_from_ai, extraction_rules)
                    
                    # Update extraction_result tuple with processed data (cleaned or original)
                    # This must happen regardless of whether rules were applied
                    extraction_result = list(extraction_result)
                    extraction_result[0] = extracted_data_from_ai  # Update extracted_data in the tuple
                    extraction_result = tuple(extraction_result)
                
                # AI processing completed successfully
            except Exception as e:
                print(f"❌ Error in AI processing during cleanup: {e}")
                raise e
            
            # Align result shape with normal flow for frontend expectations
            formatted_result = None
            if extraction_result and isinstance(extraction_result, (list, tuple)) and len(extraction_result) >= 10:
                extracted_data, document_type, document_description, plan_limit_info, original_record_count, show_upgrade_option, object_name, updated_destination_name, extraction_rules, mapping_context_summary = extraction_result[:10]
                
                # Update destination_name if it was determined from classification
                if updated_destination_name and (destination_name is None or destination_name == "unknown") and destination == "google_sheets":
                    destination_name = updated_destination_name
                
                # Use preserved document_type/document_description from task_data if available (to maintain metadata)
                # This matches Salesforce and Google Sheets cleanup flow behavior
                preserved_document_type = task_data.get("document_type")
                preserved_document_description = task_data.get("document_description")
                preserved_mapping_context = task_data.get("mapping_context_summary")
                
                # Use preserved metadata if available, otherwise use from extract_structured_data_from_sql return
                document_type = preserved_document_type or document_type
                document_description = preserved_document_description or document_description
                mapping_context_summary = preserved_mapping_context or mapping_context_summary
                
                # If not in task_data, try to get from parent task
                if not document_type or not document_description:
                    parent_job_id = task_data.get("parent_job_id")
                    if parent_job_id:
                        # Try to get from parent task (SQL uses sql_tasks / Redis)
                        parent_task = await sql_tasks.get(parent_job_id)
                        if parent_task:
                            parent_result = parent_task.get("result")
                            if isinstance(parent_result, dict):
                                if not document_type:
                                    document_type = parent_result.get("document_type")
                                if not document_description:
                                    document_description = parent_result.get("document_description")
                                if not mapping_context_summary:
                                    mapping_context_summary = parent_result.get("mapping_context_summary")
                
                # Final fallback: get from extraction_results table
                if not document_type or not document_description:
                    parent_job_id = task_data.get("parent_job_id")
                    if parent_job_id:
                        extraction_result_from_db = await ExtractionResultsService.get_extraction_result(
                            parent_job_id, user_id
                        )
                        if extraction_result_from_db:
                            if not document_type:
                                document_type = extraction_result_from_db.get("document_type")
                            if not document_description:
                                document_description = extraction_result_from_db.get("document_description")
                
                # Use preserved document_type and document_description, or fallback to defaults
                if not document_type:
                    document_type = "SQL Data"
                if not document_description:
                    document_description = f"Cleaned data from {table_name or 'selected'} table(s)"
                
                # Use extraction_rules from result (which includes applied cleanup rules)
                extraction_rules = extraction_rules or cleanup_extraction_rules
                
                formatted_result = {
                    "status": "success",
                    "data": extracted_data,
                    "total_records": len(extracted_data),
                    "table_name": table_name,
                    "table_names": selected_table_names,
                    "query": query,
                    "user_prompt": comprehensive_prompt,
                    "document_type": document_type,
                    "document_description": document_description,
                    "extraction_rules": extraction_rules if extraction_rules else None,
                    "mapping_context_summary": mapping_context_summary,
                    "plan_limit_info": plan_limit_info,
                    "show_upgrade_option": show_upgrade_option,
                    "connector_id": str(connector_id) if connector_id else None,
                    "extracted_schema_type": "sql",
                    "destination": destination,
                    "destination_name": destination_name
                }
            else:
                # Fallback if extraction fails
                formatted_result = {
                    "status": "success",
                    "data": flat_data,
                    "total_records": len(flat_data) if flat_data else 0,
                    "table_name": table_name,
                    "table_names": selected_table_names,
                    "query": query,
                    "user_prompt": comprehensive_prompt,
                    "document_type": task_data.get("document_type") or "SQL Data",
                    "document_description": task_data.get("document_description") or f"Cleaned data from {table_name or 'selected'} table(s)",
                    "extraction_rules": cleanup_extraction_rules if cleanup_extraction_rules else None,
                    "mapping_context_summary": task_data.get("mapping_context_summary"),
                    "plan_limit_info": None,
                    "show_upgrade_option": False,
                    "connector_id": str(connector_id) if connector_id else None,
                    "extracted_schema_type": "sql",
                    "destination": destination,
                    "destination_name": destination_name
                }
            
            # Continue to unified post-processing (quality analysis and save logic - same as normal flow)
            # Skip SQL execution and AI processing for cleanup flow - we already have cleaned data
        else:
            # Normal extraction flow - extract from SQL
            # For cleanup intent, check if we have cleanup_extraction_rules and use them
            cleanup_extraction_rules = task_data.get("cleanup_extraction_rules")
            if cleanup_extraction_rules:
                # Use cleanup rules for cleanup intent
                extraction_rules = cleanup_extraction_rules
            
            # Log extraction_started event with intent_state
            if conversation_id:
                try:
                    from services.chat_service import ChatService
                    from handlers.classification_handler import create_intent_state
                    
                    # Extract source_name from connector
                    source_name = None
                    if connector_data and "connector" in connector_data:
                        connector = connector_data["connector"]
                        if isinstance(connector, dict):
                            source_name = connector.get("name")
                        elif hasattr(connector, "name"):
                            source_name = connector.name
                    
                    # Determine intent (extract or migrate based on destination)
                    active_intent = "migrate" if destination and destination != "unknown" else "extract"
                    
                    intent_state = create_intent_state(
                        active_intent=active_intent,
                        active_source="sql",
                        active_destination=destination or "unknown",
                        source_name=source_name,
                        destination_name=destination_name,
                        job_id=task_id,
                        state="extracting"
                    )
                    
                    await ChatService.log_data_event(
                        conversation_id=conversation_id,
                        user_id=user_id,
                        event_type="extraction_started",
                        event_data={
                            "job_id": task_id,
                            "source_type": "sql",
                            "source_name": source_name,
                            "destination": destination,
                            "destination_name": destination_name,
                            "intent_state": intent_state
                        }
                    )
                except Exception as e:
                    print(f"⚠️ Failed to log extraction_started event: {e}")
        
        # Skip SQL execution and AI processing if cleanup flow already processed data
        if is_cleanup_flow and formatted_result:
            # Extract connector_id for formatted_result (needed for saving)
            connector_id = None
            if connector_data and "connector" in connector_data:
                connector = connector_data["connector"]
                if hasattr(connector, 'get'):
                    connector_id = connector.get('id')
                elif hasattr(connector, 'id'):
                    connector_id = connector.id
                elif isinstance(connector, dict):
                    connector_id = connector.get('id')
            # Update formatted_result with connector_id if not already set
            if connector_id and not formatted_result.get("connector_id"):
                formatted_result["connector_id"] = str(connector_id)
        else:
            # Normal flow: Extract connector_id and execute SQL query
            # Extract connector_id from connector_data
            connector_id = None
            if connector_data and "connector" in connector_data:
                connector = connector_data["connector"]
                if hasattr(connector, 'get'):
                    connector_id = connector.get('id')
                elif hasattr(connector, 'id'):
                    connector_id = connector.id
                elif isinstance(connector, dict):
                    connector_id = connector.get('id')
            
            # Connector data structure analysis
            
            if not isinstance(connector_data, dict):
                raise ValueError(f"Connector data is not a dictionary: {type(connector_data)}")
            
            if "connector" not in connector_data:
                raise ValueError(f"Connector data missing 'connector' key: {list(connector_data.keys())}")
            
            # Convert Record objects to dictionaries
            if hasattr(connector_data["connector"], '_mapping'):
                # It's a Record object, convert to dict
                connector_data["connector"] = dict(connector_data["connector"])
                # Converted connector Record to dictionary
            
            if not isinstance(connector_data["connector"], dict):
                raise ValueError(f"Connector data['connector'] is not a dictionary: {type(connector_data['connector'])}")
            
            # Also convert tables if they are Record objects
            if "tables" in connector_data and connector_data["tables"]:
                converted_tables = []
                for table in connector_data["tables"]:
                    if hasattr(table, '_mapping'):
                        converted_tables.append(dict(table))
                    else:
                        converted_tables.append(table)
                connector_data["tables"] = converted_tables
                # Converted table Records to dictionaries
            
            # Create connector instance (include Snowflake warehouse/role/schema from additional_config)
            connector_row = connector_data["connector"]
            if hasattr(connector_row, "_mapping"):
                connector_row = dict(connector_row)
            elif not isinstance(connector_row, dict):
                connector_row = dict(connector_row)

            connector_config = SQLBackgroundProcessingService().prepare_connector_config(connector_row)

            try:
                connector_instance = SQLConnectorFactory.create_connector(
                    connector_row["connector_type"],
                    connector_config,
                )
                # Connector instance created successfully
            except Exception as e:
                print(f"❌ Error creating connector instance: {e}")
                print(f"🔍 Error type: {type(e)}")
                raise e
        
            # Update status
            await _update_task_status(task_id, "processing", 30, "Executing SQL query...")
        
            # Execute SQL query
            # Support both legacy single-table and new multi-table outputs
            selected_table_names = []
            if isinstance(table_and_query, dict):
                if table_and_query.get("table_names") and isinstance(table_and_query["table_names"], list):
                    selected_table_names = table_and_query["table_names"]
                elif table_and_query.get("table_name"):
                    selected_table_names = [table_and_query["table_name"]]

            table_name = selected_table_names[0] if selected_table_names else None
            # Support both "query" and "sql_query" field names for backward compatibility
            query = table_and_query.get("query") or table_and_query.get("sql_query")
            if not query:
                raise ValueError("No query found in table_and_query. Expected 'query' or 'sql_query' field.")

            if (connector_row.get("connector_type") or "").strip().lower() == "snowflake":
                from services.sql_connector_service import qualify_snowflake_sql

                query = qualify_snowflake_sql(query, selected_table_names, connector_config)

            def _convert_datetime_for_json(obj):
                if hasattr(obj, 'isoformat'):
                    return obj.isoformat()
                if hasattr(obj, 'strftime'):
                    return obj.strftime('%Y-%m-%d %H:%M:%S')
                return obj

            # Execute query - use streaming when available to avoid loading full result in memory
            from utils.batch_config import EXTRACTION_BATCH_SIZE
            serializable_results = []
            stream_start = time.time()
            try:
                if hasattr(connector_instance, 'execute_query_iter_batches'):
                    async for batch in connector_instance.execute_query_iter_batches(
                        query, batch_size=EXTRACTION_BATCH_SIZE
                    ):
                        for row in batch:
                            if isinstance(row, dict):
                                serializable_row = {k: _convert_datetime_for_json(v) for k, v in row.items()}
                                serializable_results.append(serializable_row)
                            else:
                                serializable_results.append(_convert_datetime_for_json(row))
                        # Progress update for large extractions
                        if len(serializable_results) % (EXTRACTION_BATCH_SIZE * 2) == 0 and len(serializable_results) > 0:
                            await _update_task_status(task_id, "processing", 35, f"Fetched {len(serializable_results)} rows...")
                else:
                    sql_data = await connector_instance.execute_query(query)
                    for row in (sql_data or []):
                        if isinstance(row, dict):
                            serializable_row = {k: _convert_datetime_for_json(v) for k, v in row.items()}
                            serializable_results.append(serializable_row)
                        else:
                            serializable_results.append(_convert_datetime_for_json(row))
                if serializable_results:
                    elapsed = time.time() - stream_start
                    print(f"📊 SQL streaming fetched {len(serializable_results)} rows in {elapsed:.1f}s")
            except Exception as e:
                print(f"❌ Error executing query: {e}")
                print(f"🔍 Error type: {type(e)}")
                raise e
            
            empty_extract = False
            if not serializable_results:
                print(
                    "ℹ️ SQL query returned 0 rows — completing extraction successfully "
                    "(aligned with empty Salesforce extract; incremental filter may match no rows)"
                )
                formatted_result = {
                    "status": "success",
                    "data": [],
                    "total_records": 0,
                    "table_name": table_name,
                    "table_names": selected_table_names,
                    "query": query,
                    "user_prompt": comprehensive_prompt,
                    "document_type": "SQL Data",
                    "document_description": "No rows returned for this query",
                    "show_upgrade_option": False,
                    "connector_id": str(connector_id) if connector_id else None,
                    "extracted_schema_type": "sql",
                    "destination": destination,
                    "destination_name": destination_name,
                    "extraction_rules": extraction_rules if extraction_rules else None,
                    "message": "No rows returned from query",
                }
                empty_extract = True

            if not empty_extract:
                actual_record_count = len(serializable_results)
                print(f"📊 SQL query returned {actual_record_count} records")
        
                # Credit-based extraction limit (how many records user can afford)
                from services.architect_agent import get_user_extraction_limit
                extraction_limit = await get_user_extraction_limit(user_id)
                can_extract_count = extraction_limit.get("can_extract", 0) or 0
                message = "Insufficient credits for extraction. Add credits or upgrade your plan." if can_extract_count <= 0 else None
        
                if can_extract_count <= 0:
                    plan_info_with_flag = extraction_limit.copy()
                    plan_info_with_flag["is_plan_limit_reached"] = True
                    formatted_result = {
                        "status": "plan_limit_reached",
                        "message": message or "Insufficient credits for extraction",
                        "data": [],
                        "plan_info": plan_info_with_flag,
                        "show_upgrade_option": True,
                        "total_records": actual_record_count,
                        "table_name": table_name,
                        "table_names": selected_table_names,
                        "query": query,
                        "user_prompt": comprehensive_prompt,
                        "connector_id": str(connector_id) if connector_id else None,
                        "extracted_schema_type": "sql",
                        "extraction_rules": extraction_rules if extraction_rules else None  # Include extraction_rules even if None
                    }
                
                    await _update_task_status(task_id, "completed", 100, "Credit limit reached", result=formatted_result)
                
                    # Save extraction result (even though no data was extracted)
                    try:
                        conversation_id = task_data.get("conversation_id")
                        parent_job_id = task_data.get("parent_job_id")
                    
                        asyncio.create_task(
                            ExtractionResultsService.save_extraction_result(
                                job_id=task_id,
                                user_id=user_id,
                                extracted_data=[],
                                source_type="sql",
                                extraction_goals=comprehensive_prompt,
                                document_type="SQL Data",
                                document_description=f"Data from {table_name or 'selected'} table(s) - Credit limit reached",
                                source_name=_sql_save_source_name(connector_data, table_name=table_name),
                                source_identifier=str(connector_id) if connector_id else None,
                                parent_job_id=parent_job_id,
                                conversation_id=conversation_id,
                                has_cleanup_rules=False,
                                has_transformation_rules=False,
                                status="plan_limit_reached"
                            )
                        )
                    except Exception as save_error:
                        print(f"⚠️ Failed to save SQL extraction result (credit limit): {save_error}")
                
                    return

                # User has credits to extract - cap at credit-based can_extract
                if actual_record_count > can_extract_count:
                    serializable_results = serializable_results[:can_extract_count]
                    print(f"📊 Limiting SQL data from {actual_record_count} to {len(serializable_results)} records due to credit limit (before AI processing)")
            
                _cap = extraction_limit.get("extraction_cap")
                show_upgrade_option = extraction_limit.get(
                    "show_upgrade_option",
                    (_cap is not None and can_extract_count < _cap)
                    or extraction_limit.get("limit_reached", False),
                )
                plan_info_with_flag = extraction_limit.copy()
                plan_info_with_flag["is_plan_limit_reached"] = can_extract_count <= 0
                # Continue to extract_structured_data_from_sql - don't return early

                task_data = await sql_tasks.get(task_id)
                if task_data and task_data.get("cancelled"):
                    print(f"🛑 SQL extraction task {task_id} cancelled by user")
                    await sql_tasks.update(task_id, {"status": "cancelled"})
                    return

                # Update status
                await _update_task_status(task_id, "processing", 60, "Processing data with AI...")

                # Process data with AI using the same contract as the sync flow
                print(f"🤖 Processing {len(serializable_results)} records with AI...")
                try:
                    from handlers.sql_handler import extract_structured_data_from_sql
                    extraction_result = await extract_structured_data_from_sql(
                        serializable_results,
                        user_id,
                        comprehensive_prompt,
                        table_name,
                        destination=destination,
                        destination_name=destination_name,
                        saved_extraction_rules=extraction_rules,
                        extraction_profile=task_data.get("extraction_profile"),
                        intent=task_data.get("intent"),
                        template=task_data.get("template"),
                    )
                    # AI processing completed successfully
                except Exception as e:
                    print(f"❌ Error in AI processing: {e}")
                    raise e

                # Align result shape with execute_sql_query_and_stream for frontend expectations
                formatted_result = None
                if extraction_result and isinstance(extraction_result, (list, tuple)) and len(extraction_result) >= 10:
                    extracted_data, document_type, document_description, plan_limit_info, original_record_count, show_upgrade_option, object_name, updated_destination_name, extraction_rules, mapping_context_summary = extraction_result[:10]
                
                    # Update destination_name if it was determined from classification
                    if updated_destination_name and (destination_name is None or destination_name == "unknown") and destination == "google_sheets":
                        destination_name = updated_destination_name

                    formatted_result = {
                        "status": "success",
                        "data": extracted_data,
                        "total_records": len(extracted_data),
                        "table_name": table_name,
                        "table_names": selected_table_names,
                        "query": query,
                        "user_prompt": comprehensive_prompt,
                        "document_type": document_type,
                        "document_description": document_description,
                        "extraction_rules": extraction_rules if extraction_rules else None,
                        "mapping_context_summary": mapping_context_summary,
                        "plan_limit_info": plan_limit_info,
                        "show_upgrade_option": show_upgrade_option,
                        "connector_id": str(connector_id) if connector_id else None,
                        "extracted_schema_type": "sql",
                        "destination": destination,
                        "destination_name": destination_name
                    }
                else:
                    formatted_result = {
                        "status": "success",
                        "data": serializable_results,
                        "total_records": len(serializable_results),
                        "table_name": table_name,
                        "table_names": selected_table_names,
                        "query": query,
                        "user_prompt": comprehensive_prompt,
                        "document_type": "SQL Data",
                        "document_description": f"Data from {table_name or 'selected'} table(s)",
                        "show_upgrade_option": False,
                        "connector_id": str(connector_id) if connector_id else None,
                        "extracted_schema_type": "sql",
                        "destination": destination,
                        "destination_name": destination_name,
                        "extraction_rules": extraction_rules if extraction_rules else None  # Include extraction_rules from task_data
                    }

        connector_name = _sql_connector_display_name(connector_data)
        resolved_connector_id = (
            (formatted_result.get("connector_id") if isinstance(formatted_result, dict) else None)
            or _sql_connector_id_from_sources(connector_data, task_data)
        )
        ext_meta = _sql_persisted_extraction_metadata(
            task_data,
            table_name=formatted_result.get("table_name") if isinstance(formatted_result, dict) else None,
            table_names=formatted_result.get("table_names") if isinstance(formatted_result, dict) else None,
            connector_id=resolved_connector_id,
            connector_name=connector_name,
            sql_query=formatted_result.get("query") if isinstance(formatted_result, dict) else None,
        )
        if ext_meta and isinstance(formatted_result, dict):
            formatted_result["extraction_metadata"] = ext_meta
        
        # Run data quality analysis on extracted data (for both cleanup and normal flows)
        data_quality_report = None
        try:
            from utils.data_quality_analyzer import analyze_data_quality
            extracted_data = formatted_result.get("data", [])
            if extracted_data:
                data_quality_report = await asyncio.to_thread(analyze_data_quality, extracted_data)
                print(f"📊 Data quality analysis completed: Score {data_quality_report.get('summary', {}).get('data_quality_score', 0)}/100")
            else:
                print(f"⚠️ [DATA QUALITY] No data to analyze - extracted_data is empty")
        except Exception as quality_error:
            print(f"⚠️ Error running data quality analysis: {quality_error}")
            import traceback
            traceback.print_exc()
            # Continue without quality report if analysis fails
        
        # Log extraction_completed event FIRST (intent_state remains active until mapping/migration starts)
        # This must happen BEFORE updating task status so extraction_message_id is included in the result
        if conversation_id:
            try:
                from services.chat_service import ChatService
                from handlers.classification_handler import create_intent_state
                
                # Extract source_name from connector (connection name, not table)
                source_name = connector_name or _sql_connector_display_name(connector_data)
                
                # Determine intent (extract or migrate based on destination)
                active_intent = "migrate" if destination and destination != "unknown" else "extract"
                
                # Update intent_state - mark as completed but still active (awaiting mapping/migration)
                intent_state = create_intent_state(
                    active_intent=active_intent,
                    active_source="sql",
                    active_destination=destination or "unknown",
                    source_name=source_name,
                    destination_name=destination_name,
                    job_id=task_id,
                    state="extraction_completed"  # State changed to indicate extraction is done
                )
                
                # Debug: Log extraction_rules status before logging event
                extraction_rules_in_result = formatted_result.get("extraction_rules")
                if extraction_rules_in_result:
                    print(f"✅ [SQL EXTRACTION] extraction_rules found in formatted_result: {type(extraction_rules_in_result)}")
                    if isinstance(extraction_rules_in_result, dict):
                        transformation_rules = extraction_rules_in_result.get('transformation_rules')
                        # Null check: ensure transformation_rules exists and is a dict
                        has_transformation = bool(transformation_rules and isinstance(transformation_rules, dict))
                        print(f"🔍 [SQL EXTRACTION] Has transformation_rules in extraction_rules: {has_transformation}")
                        if has_transformation:
                            tr_fields = transformation_rules.get('fields', [])
                            print(f"🔍 [SQL EXTRACTION] transformation_rules.fields count: {len(tr_fields) if isinstance(tr_fields, list) else 0}")
                        elif transformation_rules is not None:
                            print(f"⚠️ [SQL EXTRACTION] transformation_rules exists but is not a dict (type: {type(transformation_rules)})")
                else:
                    print(f"⚠️ [SQL EXTRACTION] extraction_rules is None in formatted_result - will be None in extraction_completed event")

                from utils.document_category_match import enrich_extraction_category_fields

                sample_data = (
                    (formatted_result.get("data") or [None])[0]
                    if isinstance(formatted_result.get("data"), list)
                    else {}
                )
                category_meta = await enrich_extraction_category_fields(
                    user_id,
                    destination,
                    destination_name,
                    formatted_result.get("document_type"),
                    formatted_result.get("document_description"),
                    sample_data if isinstance(sample_data, dict) else {},
                )
                if category_meta.get("canonical_document_type"):
                    formatted_result["document_type"] = category_meta["canonical_document_type"]
                if category_meta.get("canonical_document_description"):
                    formatted_result["document_description"] = category_meta["canonical_document_description"]
                
                extraction_completed_message = await ChatService.log_data_event(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    event_type="extraction_completed",
                    event_data={
                        "job_id": task_id,
                        "source_type": "sql",
                        "source_name": source_name,
                        "record_count": formatted_result.get("total_records", 0),
                        "status": "success",
                        "destination": destination,
                        "destination_name": destination_name,
                        "document_type": formatted_result.get("document_type"),
                        "document_description": formatted_result.get("document_description"),
                        "extraction_rules": formatted_result.get("extraction_rules"),  # This will be None if not set in formatted_result
                        "mapping_context_summary": formatted_result.get("mapping_context_summary"),
                        "connector_id": resolved_connector_id,
                        "sql_query": formatted_result.get("query"),
                        "table_name": formatted_result.get("table_name"),
                        "table_names": formatted_result.get("table_names"),
                        **(ext_meta or {}),
                        "extraction_metadata": ext_meta,
                        "intent_state": intent_state,
                        "data_quality_report": data_quality_report,
                        **category_meta,
                    }
                )
                # Store message_id and intent_state in formatted_result BEFORE updating task status
                # This ensures extraction_message_id is included when task status is saved
                if extraction_completed_message and extraction_completed_message.get("id"):
                    formatted_result["extraction_message_id"] = extraction_completed_message["id"]
                    formatted_result["intent_state"] = intent_state
                    formatted_result["data_quality_report"] = data_quality_report
            except Exception as e:
                print(f"⚠️ Failed to log extraction_completed event: {e}")
        
        # Don't store full data in Redis when over threshold; frontend fetches from API
        result_for_redis = formatted_result
        sql_total = formatted_result.get("total_records", 0)
        if sql_total > 0:
            from utils.redis_service import REDIS_SLIM_RESULT_THRESHOLD
            if sql_total > REDIS_SLIM_RESULT_THRESHOLD:
                result_for_redis = {**formatted_result, "data": None, "record_count": sql_total}
                try:
                    from utils.extraction_monitoring import log_redis_slim_metrics
                    log_redis_slim_metrics(task_id, sql_total, slimmed=True, reason="sql_extraction")
                except ImportError:
                    pass

        # Save extraction to DB before marking task completed so migration sees full rows
        try:
            conversation_id = task_data.get("conversation_id")
            parent_job_id = task_data.get("parent_job_id")

            extracted_data = formatted_result.get("data", [])

            has_cleanup_rules = False
            has_transformation_rules = False
            if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                first_record = extracted_data[0]
                if isinstance(first_record, dict):
                    has_cleanup_rules = "data_cleanup_rules" in first_record
                    has_transformation_rules = "transformation_rules" in first_record

            source_name = _sql_save_source_name(connector_data, table_name=table_name)
            source_identifier = str(resolved_connector_id) if resolved_connector_id else None

            await ExtractionResultsService.save_extraction_result(
                job_id=task_id,
                user_id=user_id,
                extracted_data=extracted_data,
                source_type="sql",
                extraction_goals=comprehensive_prompt,
                document_type=formatted_result.get("document_type"),
                document_description=formatted_result.get("document_description"),
                source_name=source_name,
                source_identifier=source_identifier,
                parent_job_id=parent_job_id,
                conversation_id=conversation_id,
                has_cleanup_rules=has_cleanup_rules,
                has_transformation_rules=has_transformation_rules,
                status="success",
                extraction_metadata=formatted_result.get("extraction_metadata"),
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save SQL extraction result: {save_error}")
            # Don't fail extraction if saving fails

        # Update status with frontend-aligned result shape (now includes extraction_message_id)
        await _update_task_status(task_id, "completed", 100, "SQL extraction completed successfully", result=result_for_redis, destination_name=destination_name)

        # Charge extraction fee (idempotent per task_id)
        # Extraction is free; no credit deduction
        
    except Exception as e:
        print(f"❌ Error in SQL extraction task {task_id}: {e}")
        # Get AI-generated explanation (non-blocking, with timeout)
        # Re-fetch task_data safely (may be undefined if exception occurred before assignment)
        try:
            task_data_safe = await sql_tasks.get(task_id) or {}
        except Exception:
            task_data_safe = {}
        explanation_result = {"explanation": None, "solution": None}
        try:
            from services.error_explanation_service import explain_error

            table_name_ctx = (task_data_safe.get("table_and_query") or {}).get("table_name")
            explanation_result = await explain_error(
                raw_error=str(e),
                source="sql",
                context={
                    "object_name": table_name_ctx,
                    "operation": "extraction",
                    "user_query": task_data_safe.get("comprehensive_prompt"),
                },
            )
        except Exception as explain_err:
            print(f"⚠️ Error explanation failed: {explain_err}")
        await _update_task_status(
            task_id,
            "failed",
            100,
            f"SQL extraction failed: {str(e)}",
            error=str(e),
            error_explanation=explanation_result.get("explanation"),
            error_solution=explanation_result.get("solution"),
        )

        # Save failed extraction result to database (for debugging)
        try:
            conversation_id = task_data_safe.get("conversation_id")
            parent_job_id = task_data_safe.get("parent_job_id")

            # Extract source information
            table_name_safe = (task_data_safe.get("table_and_query") or {}).get("table_name")
            connector_data_safe = task_data_safe.get("connector_data")
            source_name = _sql_save_source_name(connector_data_safe, table_name=table_name_safe)
            connector_id_safe = _sql_connector_id_from_sources(connector_data_safe, task_data_safe)
            source_identifier = str(connector_id_safe) if connector_id_safe else None

            asyncio.create_task(
                ExtractionResultsService.save_extraction_result(
                    job_id=task_id,
                    user_id=task_data_safe.get("user_id", "unknown"),
                    extracted_data=[],
                    source_type="sql",
                    extraction_goals=task_data_safe.get("comprehensive_prompt"),
                    document_type=None,
                    document_description=None,
                    source_name=source_name,
                    source_identifier=source_identifier,
                    parent_job_id=parent_job_id,
                    conversation_id=conversation_id,
                    has_cleanup_rules=False,
                    has_transformation_rules=False,
                    status="failed",
                    error_message=str(e)
                )
            )
        except Exception as save_error:
            print(f"⚠️ Failed to save failed SQL extraction result: {save_error}")
            # Don't fail if saving fails
    
    finally:
        # Always close the connection to prevent resource leaks
        try:
            if 'connector_instance' in locals() and connector_instance:
                await connector_instance.close_connection()
                print(f"🔒 Connection closed for task {task_id}")
        except Exception as close_error:
            print(f"⚠️ Warning: Error closing connection for task {task_id}: {close_error}")

async def _update_task_status(
    task_id: str,
    status: str,
    progress: int,
    message: str,
    result: dict = None,
    error: str = None,
    destination_name: str = None,
    error_explanation: str = None,
    error_solution: str = None,
):
    """Update task status"""
    update_dict = {
        "status": status,
        "progress": progress,
        "message": message,
        "result": result,
        "error": error,
        "error_explanation": error_explanation,
        "error_solution": error_solution,
        "updated_at": datetime.now().isoformat(),
    }

    # Update top-level destination_name if provided
    if destination_name is not None:
        update_dict["destination_name"] = destination_name

    # Exclude result/error when None to avoid overwriting existing values
    excludes = ("result", "error", "error_explanation", "error_solution")
    updates_to_apply = {k: v for k, v in update_dict.items() if not (k in excludes and v is None)}
    existing = await sql_tasks.get(task_id)
    if existing is not None:
        await sql_tasks.update(task_id, updates_to_apply)
        print(f"📊 Task {task_id}: {status} - {progress}% - {message}")

async def get_task_status(task_id: str) -> Optional[Dict[str, Any]]:
    """Get SQL extraction task status"""
    return await sql_tasks.get(task_id)

# Create service instance
sql_background_service = SQLBackgroundProcessingService()
