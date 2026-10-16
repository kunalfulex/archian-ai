import logging
import os
import uuid
from uuid import UUID
from collections import defaultdict, deque
from datetime import datetime, date
from typing import Dict, Any, List, Optional, Tuple, Set

logger = logging.getLogger(__name__)


def _migration_verbose() -> bool:
    return (os.getenv("SQL_MIGRATE_DEBUG") or "").strip().lower() in ("1", "true", "yes", "on")


def _migration_debug(msg: str, *args) -> None:
    if _migration_verbose():
        logger.info(msg, *args)


def _migration_record_error(msg: str) -> None:
    logger.debug(msg)

try:
    from pymysql.err import IntegrityError as MySQLIntegrityError
except ImportError:
    MySQLIntegrityError = Exception  # type: ignore

try:
    from aiomysql import IntegrityError as AioMySQLIntegrityError  # type: ignore
except ImportError:
    AioMySQLIntegrityError = Exception  # type: ignore

try:
    from psycopg2 import IntegrityError as PostgresIntegrityError  # type: ignore
    from psycopg2.errors import UniqueViolation  # type: ignore
except ImportError:
    PostgresIntegrityError = Exception  # type: ignore
    UniqueViolation = Exception  # type: ignore

try:
    from asyncpg import UniqueViolationError  # type: ignore
except ImportError:
    UniqueViolationError = Exception  # type: ignore

from services.sql_connector_service import SQLConnectorService
from services.entitlements import get_entitlements
from services.migration_records_service import MigrationRecordsService
from utils.data_type_converter import convert_value_for_database


def _normalize_flat_field_key(name: str) -> str:
    """Lowercase alphanumerics only — 'Email Address' -> 'emailaddress'."""
    if not name or not isinstance(name, str):
        return ""
    return "".join(c.lower() for c in name if c.isalnum())


def _get_source_value_for_mapping(record: Dict[str, Any], field_name: str) -> Any:
    """
    Resolve source value for a mapping entry key.
    Extraction JSON keys may differ only by case from the saved mapping (e.g. Email vs email).
    """
    if field_name in record:
        return record[field_name]
    fl = field_name.lower()
    for k in record:
        if isinstance(k, str) and k.lower() == fl:
            return record[k]
    return None


def _fallback_value_for_email_column(record: Dict[str, Any]) -> Any:
    """
    When the mapped field is empty but the destination column is `email`, try other
    common extraction keys so NOT NULL inserts do not fail on harmless key drift.
    """
    synonyms = frozenset(
        {
            "email",
            "emailaddress",
            "e_mail",
            "mailaddress",
            "contactemail",
            "workemail",
            "personemail",
            "primaryemail",
            "emailaddr",
            "useremail",
            "customeremail",
            "contactpersonemail",
        }
    )
    best: Any = None
    for k in record:
        if not isinstance(k, str):
            continue
        nk = _normalize_flat_field_key(k)
        if nk not in synonyms and "email" not in nk:
            continue
        val = record.get(k)
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        if nk == "email":
            return val
        if best is None:
            best = val
    return best


def _value_is_non_empty(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return bool(v.strip())
    return True


class DatabaseMigrationService:
    """Service for migrating records into SQL databases."""
    
    # Batch size for bulk operations
    # PostgreSQL: 500 records (32,767 parameter limit)
    # MySQL: 500 records (65,535 parameter limit)
    BATCH_SIZE = 500
    
    def __init__(self):
        """Initialize DatabaseMigrationService."""

    async def migrate_data_to_database(
        self,
        data: List[Dict[str, Any]],
        mapping: Dict[str, Dict[str, Any]],
        connector_id: str,
        table_name: str,
        user_id: str,
        operation: str = "append",
        batch_id: Optional[str] = None,
        document_type: Optional[str] = None,
        start_index: int = 0,
        destination_name: Optional[str] = None,
        migration_job_id: Optional[str] = None,
        job_store=None,
    ) -> Dict[str, Any]:
        connector_instance = None
        try:
            service = SQLConnectorService()
            connector_bundle = await service.get_connector_by_id(connector_id, user_id)
            if not connector_bundle:
                raise ValueError("Database connector not found or inactive")

            connector_record = dict(connector_bundle["connector"])
            connector_config = service.prepare_connector_config(connector_record)
            connector_type = str(connector_record.get("connector_type", "")).lower()

            connector_instance = service.create_connector_instance(
                connector_record["connector_type"],
                connector_config,
            )

            mapping_entries, key_columns = self._prepare_mapping_entries(mapping, table_name)
            lookup_fields = [
                {
                    "field": e.get("field"),
                    "table": e.get("table"),
                    "column": e.get("column"),
                    "lookup_of": e.get("lookup_of"),
                }
                for e in mapping_entries
                if (e.get("lookup_of") or "").strip()
            ]
            _migration_debug(f"[SQL_MIGRATE_DEBUG] migrate_data_to_database table_name={table_name!r} "
                f"entries={len(mapping_entries)} with_lookup_of={len(lookup_fields)} "
                f"lookup_sample={lookup_fields[:8]}")
            if self._mapping_entries_use_sql_lookup_of(mapping_entries):
                _migration_debug("[SQL_MIGRATE_DEBUG] using _migrate_sql_with_lookup_relationships")
                return await self._migrate_sql_with_lookup_relationships(
                    data=data,
                    mapping_entries=mapping_entries,
                    connector_instance=connector_instance,
                    connector_type=connector_type,
                    connector_id=connector_id,
                    table_name=table_name,
                    user_id=user_id,
                    operation=operation,
                    batch_id=batch_id,
                    document_type=document_type,
                    start_index=start_index,
                    destination_name=destination_name,
                    migration_job_id=migration_job_id,
                    job_store=job_store,
                )

            # Use the first mapping entry table if available to ensure we target the actual destination table
            effective_table_name = mapping_entries[0]["table"] if mapping_entries else table_name
            quoted_table_name = self._quote_identifier(connector_type, effective_table_name)
            effective_operation = self._determine_operation(operation, key_columns)

            # Fetch table schema for type conversion
            column_types: Dict[str, Dict[str, Any]] = {}
            try:
                table_schema = await connector_instance.get_table_schema(effective_table_name)
                if table_schema and 'columns' in table_schema:
                    for col in table_schema['columns']:
                        column_types[col['column_name']] = {
                            'data_type': col.get('data_type', ''),
                            'is_nullable': col.get('is_nullable', True),
                            'is_primary_key': col.get('is_primary_key', False)
                        }
                    _migration_debug(f"✅ Fetched schema for table {effective_table_name}: {len(column_types)} columns")
                    
                    # Extract primary key information from schema
                    primary_key_info = self._get_primary_key_column(column_types)
                    primary_key_column = primary_key_info[0] if primary_key_info else None
                    primary_key_data_type = primary_key_info[1] if primary_key_info else None
                    
                    if primary_key_column:
                        _migration_debug(f"✅ Primary key column: {primary_key_column} ({primary_key_data_type})")
                    else:
                        logger.debug(f"⚠️ No primary key found in schema, using fallback 'id'")
                else:
                    logger.debug(f"⚠️ Could not fetch schema for table {effective_table_name}, proceeding without type conversion")
                    primary_key_column = None
                    primary_key_data_type = None
            except Exception as e:
                logger.debug(f"⚠️ Error fetching schema for table {effective_table_name}: {e}, proceeding without type conversion")
                primary_key_column = None
                primary_key_data_type = None

            if not batch_id:
                batch_id = await MigrationRecordsService.create_migration_batch(
                    user_id=user_id,
                    batch_name=f"SQL_{effective_table_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                    document_type=document_type or "SQL Migration",
                    total_records=len(data),
                    mapping_config={
                        "connector_id": connector_id,
                        "table_name": effective_table_name,
                        "destination_name": destination_name,
                    },
                    destination_type="sql",
                )

            if effective_operation == "overwrite":
                await connector_instance.execute_non_query(f"DELETE FROM {quoted_table_name}")

            insert_count = 0
            update_count = 0
            error_count = 0
            formatted_results: List[Dict[str, Dict[str, Any]]] = []
            record_results: List[Dict[str, Any]] = []

            # Separate records into insert-only and upsert batches
            insert_batch: List[Tuple[int, Dict[str, Any]]] = []  # (index, row_values)
            upsert_records: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []  # (index, record, row_values)
            
            # First pass: Build row values and categorize records
            for idx, record in enumerate(data):
                row_values = self._build_row_values(
                    record, 
                    mapping_entries, 
                    column_types=column_types,
                    connector_type=connector_type
                )
                if not row_values:
                    formatted_results.append(
                        {
                            effective_table_name: {
                                "status": "skipped",
                                "message": "No mapped values for this record",
                                "record_index": start_index + idx + 1,
                            }
                        }
                    )
                    record_results.append(
                        {
                            "record_index": start_index + idx,
                            "source_record": record,
                            "mapped_row": {},
                            "status": "skipped",
                            "message": "No mapped values for this record",
                        }
                    )
                    continue
                
                # Categorize: upsert or insert-only
                if effective_operation == "upsert" and key_columns:
                    upsert_records.append((idx, record, row_values))
                else:
                    insert_batch.append((idx, row_values))
            
            # Create cancel_check when migration_job_id and job_store are provided (background migration)
            cancel_check = None
            if migration_job_id and job_store:
                from utils.migration_cancel import make_cancel_check
                cancel_check = make_cancel_check(migration_job_id, job_store)

            # Process insert-only records in batches
            if insert_batch:
                _migration_debug(f"📦 Processing {len(insert_batch)} insert-only records in batches of {self.BATCH_SIZE}")
                for batch_start in range(0, len(insert_batch), self.BATCH_SIZE):
                    if cancel_check and await cancel_check():
                        from utils.migration_cancel import MigrationCancelledError
                        raise MigrationCancelledError("Migration cancelled by user")
                    batch_end = min(batch_start + self.BATCH_SIZE, len(insert_batch))
                    batch = insert_batch[batch_start:batch_end]
                    
                    # Extract row values for batch insert
                    batch_rows = [row_values for _, row_values in batch]
                    batch_indices = [idx for idx, _ in batch]
                    batch_records = [data[idx] for idx in batch_indices]
                    
                    try:
                        # Execute batch insert
                        inserted = await self._insert_batch(
                            connector_instance,
                            quoted_table_name,
                            batch_rows,
                            connector_type,
                            column_types,
                        )
                        
                        # Track successful inserts
                        for i, (orig_idx, _) in enumerate(batch):
                            insert_count += 1
                            formatted_results.append(
                                {
                                    effective_table_name: {
                                        "status": "created",
                                        "message": "Successfully created in Database",
                                        "record_index": start_index + orig_idx + 1,
                                    }
                                }
                            )
                            record_results.append(
                                {
                                    "record_index": start_index + orig_idx,
                                    "source_record": batch_records[i],
                                    "mapped_row": batch_rows[i],
                                    "status": "created",
                                    "message": "Successfully created in Database",
                                }
                            )
                        
                        _migration_debug(f"✅ Batch insert successful: {inserted} records inserted (batch {batch_start // self.BATCH_SIZE + 1})")
                    
                    except Exception as batch_exc:
                        # Batch insert failed - fall back to individual inserts for this batch
                        logger.warning("Batch insert failed, falling back to individual inserts: %s", batch_exc)
                        for i, (orig_idx, row_values) in enumerate(batch):
                            try:
                                await self._insert_record(
                                    connector_instance,
                                    quoted_table_name,
                                    row_values,
                                    connector_type,
                                    column_types,
                                )
                                insert_count += 1
                                formatted_results.append(
                                    {
                                        effective_table_name: {
                                            "status": "created",
                                            "message": "Successfully created in Database",
                                            "record_index": start_index + orig_idx + 1,
                                        }
                                    }
                                )
                                record_results.append(
                                    {
                                        "record_index": start_index + orig_idx,
                                        "source_record": batch_records[i],
                                        "mapped_row": row_values,
                                        "status": "created",
                                        "message": "Successfully created in Database",
                                    }
                                )
                            except Exception as exc:
                                error_count += 1
                                error_message = f"Error inserting record: {exc}"
                                _migration_record_error(error_message)
                                formatted_results.append(
                                    {
                                        effective_table_name: {
                                            "status": "error",
                                            "message": error_message,
                                            "record_index": start_index + orig_idx + 1,
                                        }
                                    }
                                )
                                record_results.append(
                                    {
                                        "record_index": start_index + orig_idx,
                                        "source_record": batch_records[i],
                                        "mapped_row": row_values,
                                        "status": "error",
                                        "message": error_message,
                                    }
                                )
            
            # Process upsert records using query-first pattern (like Salesforce/Google Sheets)
            if upsert_records:
                if cancel_check and await cancel_check():
                    from utils.migration_cancel import MigrationCancelledError
                    raise MigrationCancelledError("Migration cancelled by user")
                _migration_debug(f"🔄 Processing {len(upsert_records)} upsert records using query-first pattern...")
                
                # Extract all records and row_values
                all_upsert_records = [record for _, record, _ in upsert_records]
                all_upsert_row_values = [row_values for _, _, row_values in upsert_records]
                all_upsert_indices = [idx for idx, _, _ in upsert_records]
                
                try:
                    # Step 1: Query existing records using duplicate check fields
                    _migration_debug(f"   🔎 Querying existing records using key columns: {', '.join(key_columns)}")
                    existing_records = await self._query_existing_records(
                        connector_instance,
                        quoted_table_name,
                        all_upsert_row_values,
                        key_columns,
                        connector_type,
                        column_types,
                        primary_key_column,
                    )
                    _migration_debug(f"   ✅ Found {len(existing_records)} existing records")
                    
                    # Step 2: Build ID mapping from query results
                    duplicate_check_id_mapping, full_field_id_mapping = self._build_id_mapping_from_query_results(
                        existing_records,
                        key_columns,
                        column_types,
                        primary_key_column,
                    )
                    _migration_debug(f"   🗺️ Built ID mapping: {len(duplicate_check_id_mapping)} records (key columns), {len(full_field_id_mapping)} records (all fields)")
                    
                    # Step 3: Split records into update vs insert (with index tracking)
                    # We need to track original indices because records will be modified (id added)
                    records_with_indices = [(i, rv) for i, rv in enumerate(all_upsert_row_values)]
                    records_to_update_with_idx, records_to_insert_with_idx = self._split_records_for_bulk_operations_with_indices(
                        records_with_indices,
                        key_columns,
                        duplicate_check_id_mapping,
                        full_field_id_mapping,
                        primary_key_column,
                    )
                    _migration_debug(f"   📊 Split records: {len(records_to_update_with_idx)} to update, {len(records_to_insert_with_idx)} to insert")
                    
                    # Verify no duplicates between update and insert lists
                    update_indices = set(idx for idx, _ in records_to_update_with_idx)
                    insert_indices = set(idx for idx, _ in records_to_insert_with_idx)
                    overlap = update_indices & insert_indices
                    if overlap:
                        logger.warning(f"   ⚠️ WARNING: Found {len(overlap)} records in both update and insert lists! This should not happen.")
                        # Remove duplicates from insert list (prefer update over insert)
                        records_to_insert_with_idx = [(idx, rec) for idx, rec in records_to_insert_with_idx if idx not in overlap]
                        _migration_debug(f"   🔧 Removed duplicates from insert list. New counts: {len(records_to_update_with_idx)} to update, {len(records_to_insert_with_idx)} to insert")
                    
                    # Verify all records are accounted for
                    total_split = len(records_to_update_with_idx) + len(records_to_insert_with_idx)
                    if total_split != len(all_upsert_row_values):
                        logger.warning(f"   ⚠️ WARNING: Record count mismatch! Expected {len(all_upsert_row_values)}, got {total_split} after split")
                    
                    # Step 4: Batch update all matched records
                    if records_to_update_with_idx:
                        _migration_debug(f"   🔄 Batch updating {len(records_to_update_with_idx)} records...")
                        # Extract just the records (without indices) for batch update
                        records_to_update = [record for _, record in records_to_update_with_idx]
                        
                        # Process in batches
                        for batch_start in range(0, len(records_to_update), self.BATCH_SIZE):
                            if cancel_check and await cancel_check():
                                from utils.migration_cancel import MigrationCancelledError
                                raise MigrationCancelledError("Migration cancelled by user")
                            batch_end = min(batch_start + self.BATCH_SIZE, len(records_to_update))
                            batch = records_to_update[batch_start:batch_end]
                            batch_with_indices = records_to_update_with_idx[batch_start:batch_end]
                            
                            try:
                                updated = await self._update_batch(
                                    connector_instance,
                                    quoted_table_name,
                                    batch,
                                    key_columns,
                                    connector_type,
                                    column_types,
                                    primary_key_column,
                                    primary_key_data_type,
                                )
                                
                                # Track successful updates using the tracked indices
                                for (orig_list_idx, row_values) in batch_with_indices:
                                    # Get the original record index from upsert_records
                                    orig_idx = all_upsert_indices[orig_list_idx]
                                    orig_record = all_upsert_records[orig_list_idx]
                                    
                                    update_count += 1
                                    formatted_results.append(
                                        {
                                            effective_table_name: {
                                                "status": "updated",
                                                "message": "Successfully updated in Database",
                                                "record_index": start_index + orig_idx + 1,
                                            }
                                        }
                                    )
                                    record_results.append(
                                        {
                                            "record_index": start_index + orig_idx,
                                            "source_record": orig_record,
                                            "mapped_row": row_values,
                                            "status": "updated",
                                            "message": "Successfully updated in Database",
                                        }
                                    )
                                
                                _migration_debug(f"   ✅ Batch update successful: {updated} records updated (batch {batch_start // self.BATCH_SIZE + 1})")
                            
                            except Exception as batch_exc:
                                logger.warning("Batch update failed, falling back to individual updates: %s", batch_exc)
                                # Fall back to individual updates using tracked indices
                                for (orig_list_idx, row_values) in batch_with_indices:
                                    try:
                                        # Get the original record index from upsert_records
                                        orig_idx = all_upsert_indices[orig_list_idx]
                                        orig_record = all_upsert_records[orig_list_idx]
                                        
                                        # Remove 'id' from row_values for individual upsert (it will query again)
                                        row_values_for_upsert = {k: v for k, v in row_values.items() if k.lower() != 'id'}
                                        
                                        if orig_idx is not None:
                                            # Use individual upsert method (without id, it will query again)
                                            upsert_result = await self._upsert_record(
                                                connector_instance,
                                                quoted_table_name,
                                                row_values_for_upsert,
                                                key_columns,
                                                connector_type,
                                                column_types,
                                            )
                                            if upsert_result == "updated":
                                                update_count += 1
                                                status = "updated"
                                                message = "Successfully updated in Database"
                                            else:
                                                insert_count += 1
                                                status = "created"
                                                message = "Successfully created in Database"
                                            
                                            formatted_results.append(
                                                {
                                                    effective_table_name: {
                                                        "status": status,
                                                        "message": message,
                                                        "record_index": start_index + orig_idx + 1,
                                                    }
                                                }
                                            )
                                            record_results.append(
                                                {
                                                    "record_index": start_index + orig_idx,
                                                    "source_record": orig_record,
                                                    "mapped_row": row_values,
                                                    "status": status,
                                                    "message": message,
                                                }
                                            )
                                    except Exception as exc:
                                        error_count += 1
                                        error_message = f"Error upserting record: {exc}"
                                        _migration_record_error(error_message)
                                        # Use tracked index
                                        orig_idx = all_upsert_indices[orig_list_idx]
                                        orig_record = all_upsert_records[orig_list_idx]
                                        
                                        if orig_idx is not None:
                                            formatted_results.append(
                                                {
                                                    effective_table_name: {
                                                        "status": "error",
                                                        "message": error_message,
                                                        "record_index": start_index + orig_idx + 1,
                                                    }
                                                }
                                            )
                                            record_results.append(
                                                {
                                                    "record_index": start_index + orig_idx,
                                                    "source_record": orig_record,
                                                    "mapped_row": row_values,
                                                    "status": "error",
                                                    "message": error_message,
                                                }
                                            )
                    
                    # Step 5: Batch insert all unmatched records
                    if records_to_insert_with_idx:
                        _migration_debug(f"   ➕ Batch inserting {len(records_to_insert_with_idx)} records...")
                        # Extract just the records (without indices) for batch insert
                        records_to_insert = [record for _, record in records_to_insert_with_idx]
                        
                        # Process in batches
                        for batch_start in range(0, len(records_to_insert), self.BATCH_SIZE):
                            if cancel_check and await cancel_check():
                                from utils.migration_cancel import MigrationCancelledError
                                raise MigrationCancelledError("Migration cancelled by user")
                            batch_end = min(batch_start + self.BATCH_SIZE, len(records_to_insert))
                            batch = records_to_insert[batch_start:batch_end]
                            batch_with_indices = records_to_insert_with_idx[batch_start:batch_end]
                            
                            try:
                                inserted = await self._insert_batch(
                                    connector_instance,
                                    quoted_table_name,
                                    batch,
                                    connector_type,
                                    column_types,
                                )
                                
                                # Track successful inserts using the tracked indices
                                for (orig_list_idx, row_values) in batch_with_indices:
                                    # Get the original record index from upsert_records
                                    orig_idx = all_upsert_indices[orig_list_idx]
                                    orig_record = all_upsert_records[orig_list_idx]
                                    
                                    insert_count += 1
                                    formatted_results.append(
                                        {
                                            effective_table_name: {
                                                "status": "created",
                                                "message": "Successfully created in Database",
                                                "record_index": start_index + orig_idx + 1,
                                            }
                                        }
                                    )
                                    record_results.append(
                                        {
                                            "record_index": start_index + orig_idx,
                                            "source_record": orig_record,
                                            "mapped_row": row_values,
                                            "status": "created",
                                            "message": "Successfully created in Database",
                                        }
                                    )
                                
                                _migration_debug(f"   ✅ Batch insert successful: {inserted} records inserted (batch {batch_start // self.BATCH_SIZE + 1})")
                            
                            except Exception as batch_exc:
                                logger.warning("Batch insert failed, falling back to individual inserts: %s", batch_exc)
                                # Fall back to individual inserts using tracked indices
                                for (orig_list_idx, row_values) in batch_with_indices:
                                    try:
                                        # Get the original record index from upsert_records
                                        orig_idx = all_upsert_indices[orig_list_idx]
                                        orig_record = all_upsert_records[orig_list_idx]
                                        
                                        if orig_idx is not None:
                                            await self._insert_record(
                                                connector_instance,
                                                quoted_table_name,
                                                row_values,
                                                connector_type,
                                                column_types,
                                            )
                                            insert_count += 1
                                            formatted_results.append(
                                                {
                                                    effective_table_name: {
                                                        "status": "created",
                                                        "message": "Successfully created in Database",
                                                        "record_index": start_index + orig_idx + 1,
                                                    }
                                                }
                                            )
                                            record_results.append(
                                                {
                                                    "record_index": start_index + orig_idx,
                                                    "source_record": orig_record,
                                                    "mapped_row": row_values,
                                                    "status": "created",
                                                    "message": "Successfully created in Database",
                                                }
                                            )
                                    except Exception as exc:
                                        error_count += 1
                                        error_message = f"Error inserting record: {exc}"
                                        _migration_record_error(error_message)
                                        # Use tracked index
                                        orig_idx = all_upsert_indices[orig_list_idx]
                                        orig_record = all_upsert_records[orig_list_idx]
                                        
                                        if orig_idx is not None:
                                            formatted_results.append(
                                                {
                                                    effective_table_name: {
                                                        "status": "error",
                                                        "message": error_message,
                                                        "record_index": start_index + orig_idx + 1,
                                                    }
                                                }
                                            )
                                            record_results.append(
                                                {
                                                    "record_index": start_index + orig_idx,
                                                    "source_record": orig_record,
                                                    "mapped_row": row_values,
                                                    "status": "error",
                                                    "message": error_message,
                                                }
                                            )
                    
                
                except Exception as e:
                    # Query-first pattern failed - fall back to individual upserts
                    logger.warning("Query-first upsert failed, falling back to legacy approach: %s", e)
                    import traceback
                    traceback.print_exc()
                    can_batch_upsert = connector_type.lower() in ['mysql', 'mariadb', 'postgresql', 'postgres', 'mssql', 'sqlserver']
                    
                    if can_batch_upsert:
                        # Process in batches using batch upsert
                        for batch_start in range(0, len(upsert_records), self.BATCH_SIZE):
                            batch_end = min(batch_start + self.BATCH_SIZE, len(upsert_records))
                            batch = upsert_records[batch_start:batch_end]
                            
                            # Extract data for batch
                            batch_rows = [row_values for _, _, row_values in batch]
                            batch_indices = [idx for idx, _, _ in batch]
                            batch_records = [record for _, record, _ in batch]
                            
                            try:
                                # Execute batch upsert
                                upsert_results = await self._upsert_batch(
                                    connector_instance,
                                    quoted_table_name,
                                    batch_rows,
                                    key_columns,
                                    connector_type,
                                    column_types,
                                )
                                
                                # Batch upsert doesn't tell us which were inserted vs updated
                                # We'll mark all as "upserted" (created/updated)
                                # For now, we'll assume they were all successful and mark as "updated"
                                # (since upsert typically means update if exists, insert if not)
                                for i, (orig_idx, record, row_values) in enumerate(batch):
                                    # We can't distinguish insert vs update in batch, so mark as "updated"
                                    # (upsert operation - record was either created or updated)
                                    update_count += 1
                                    status = "updated"
                                    message = "Successfully upserted in Database"
                                    
                                    formatted_results.append(
                                        {
                                            effective_table_name: {
                                                "status": status,
                                                "message": message,
                                                "record_index": start_index + orig_idx + 1,
                                            }
                                        }
                                    )
                                    
                                    record_results.append(
                                        {
                                            "record_index": start_index + orig_idx,
                                            "source_record": record,
                                            "mapped_row": row_values,
                                            "status": status,
                                            "message": message,
                                        }
                                    )
                                
                                _migration_debug(f"✅ Batch upsert successful: {len(batch)} records upserted (batch {batch_start // self.BATCH_SIZE + 1})")
                            
                            except Exception as batch_exc:
                                # Batch upsert failed - fall back to individual upserts for this batch
                                error_msg = str(batch_exc).lower()
                                if "no unique or exclusion constraint" in error_msg or "there is no unique constraint" in error_msg or "postgresql on conflict requires" in error_msg:
                                    logger.warning(
                                        "PostgreSQL batch upsert needs unique constraint on %s; falling back to individual upserts",
                                        ", ".join(key_columns),
                                    )
                                else:
                                    logger.warning("Batch upsert failed, falling back to individual upserts: %s", batch_exc)
                                
                                for orig_idx, record, row_values in batch:
                                    try:
                                        upsert_result = await self._upsert_record(
                                            connector_instance,
                                            quoted_table_name,
                                            row_values,
                                            key_columns,
                                            connector_type,
                                            column_types,
                                        )
                                        if upsert_result == "updated":
                                            update_count += 1
                                            status = "updated"
                                            message = "Successfully updated in Database"
                                        elif upsert_result == "inserted":
                                            insert_count += 1
                                            status = "created"
                                            message = "Successfully created in Database"
                                        else:
                                            await self._insert_record(
                                                connector_instance,
                                                quoted_table_name,
                                                row_values,
                                                connector_type,
                                                column_types,
                                            )
                                            insert_count += 1
                                            status = "created"
                                            message = "Successfully created in Database"
                                        
                                        formatted_results.append(
                                            {
                                                effective_table_name: {
                                                    "status": status,
                                                    "message": message,
                                                    "record_index": start_index + orig_idx + 1,
                                                }
                                            }
                                        )
                                        
                                        record_results.append(
                                            {
                                                "record_index": start_index + orig_idx,
                                                "source_record": record,
                                                "mapped_row": row_values,
                                                "status": status,
                                                "message": message,
                                            }
                                        )
                                    except Exception as exc:
                                        error_count += 1
                                        error_message = f"Error upserting record: {exc}"
                                        _migration_record_error(error_message)
                                        formatted_results.append(
                                            {
                                                effective_table_name: {
                                                    "status": "error",
                                                    "message": error_message,
                                                    "record_index": start_index + orig_idx + 1,
                                                }
                                            }
                                        )
                                        record_results.append(
                                            {
                                                "record_index": start_index + orig_idx,
                                                "source_record": record,
                                                "mapped_row": row_values,
                                                "status": "error",
                                                "message": error_message,
                                            }
                                        )
                    else:
                        # Database doesn't support batch upsert - process individually
                        logger.warning("Batch upsert not supported for %s, processing individually", connector_type)
                    for idx, record, row_values in upsert_records:
                        try:
                            upsert_result = await self._upsert_record(
                                connector_instance,
                                quoted_table_name,
                                row_values,
                                key_columns,
                                connector_type,
                                column_types,  # Pass column_types for TRIM() logic in WHERE clause
                            )
                            if upsert_result == "updated":
                                update_count += 1
                                status = "updated"
                                message = "Successfully updated in Database"
                            elif upsert_result == "inserted":
                                insert_count += 1
                                status = "created"
                                message = "Successfully created in Database"
                            else:
                                await self._insert_record(
                                    connector_instance,
                                    quoted_table_name,
                                    row_values,
                                    connector_type,
                                    column_types,
                                )
                                insert_count += 1
                                status = "created"
                                message = "Successfully created in Database"

                            formatted_results.append(
                                {
                                    effective_table_name: {
                                        "status": status,
                                        "message": message,
                                        "record_index": start_index + idx + 1,
                                    }
                                }
                            )

                            record_results.append(
                                {
                                    "record_index": start_index + idx,
                                    "source_record": record,
                                    "mapped_row": row_values,
                                    "status": status,
                                    "message": message,
                                }
                            )
                        except (
                            MySQLIntegrityError,
                            AioMySQLIntegrityError,
                            PostgresIntegrityError,
                            UniqueViolation,
                            UniqueViolationError,
                        ) as integrity_exc:
                            error_count += 1
                            error_message = f"Integrity error while processing record: {integrity_exc}"
                            _migration_record_error(error_message)
                            formatted_results.append(
                                {
                                    effective_table_name: {
                                        "status": "error",
                                        "message": error_message,
                                        "record_index": start_index + idx + 1,
                                    }
                                }
                            )
                            record_results.append(
                                {
                                    "record_index": start_index + idx,
                                    "source_record": record,
                                    "mapped_row": row_values,
                                    "status": "error",
                                    "message": error_message,
                                }
                            )
                        except Exception as exc:
                            error_count += 1
                            error_message = f"Unexpected error while processing record: {exc}"
                            _migration_record_error(error_message)
                            formatted_results.append(
                                {
                                    effective_table_name: {
                                        "status": "error",
                                        "message": error_message,
                                        "record_index": start_index + idx + 1,
                                    }
                                }
                            )
                            record_results.append(
                                {
                                    "record_index": start_index + idx,
                                    "source_record": record,
                                    "mapped_row": row_values,
                                    "status": "error",
                                    "message": error_message,
                                }
                            )

            # Old duplicate loop code removed - batch processing is handled above

            processed_records = insert_count + update_count

            await MigrationRecordsService.update_batch_status(
                batch_id=batch_id,
                status="completed",
                successful_count=processed_records,
                failed_count=error_count,
            )

            await self._track_migration_records(
                batch_id=batch_id,
                user_id=user_id,
                table_name=effective_table_name,
                connector_id=connector_id,
                record_results=record_results,
                destination_name=destination_name,
            )

            if processed_records:
                await get_entitlements().track_successful_migration(user_id, processed_records)

            logger.info(
                "SQL migration batch %s table=%s inserted=%d updated=%d failed=%d",
                batch_id,
                effective_table_name,
                insert_count,
                update_count,
                error_count,
            )
            if error_count:
                logger.info(
                    "SQL migration had %d record errors (set LOG_LEVEL=DEBUG for per-record detail)",
                    error_count,
                )

            return {
                "status": "success",
                "batch_id": batch_id,
                "records_migrated": processed_records,
                "operation": effective_operation,
                "rows_inserted": insert_count,
                "rows_updated": update_count,
                "connector_id": connector_id,
                "table_name": effective_table_name,
                "destination_name": destination_name,
                "formatted_results": formatted_results,
                "result": {
                    "formatted_results": formatted_results,
                },
            }
        finally:
            if connector_instance:
                try:
                    await connector_instance.close_connection()
                except Exception:
                    pass

    def _mapping_entries_use_sql_lookup_of(self, mapping_entries: List[Dict[str, Any]]) -> bool:
        return any((e.get("lookup_of") or "").strip() for e in mapping_entries)

    @staticmethod
    def _parse_sql_lookup_of(lookup_of: Optional[str]) -> Optional[Tuple[str, str]]:
        """
        Parse lookup_of as "child_table.fk_column" (final segment is the FK column name).
        Example: user_plans.user_id -> ("user_plans", "user_id").
        """
        if not lookup_of or not isinstance(lookup_of, str):
            return None
        s = lookup_of.strip()
        if "." not in s:
            return None
        child_table, fk_column = s.rsplit(".", 1)
        child_table = child_table.strip()
        fk_column = fk_column.strip()
        if not child_table or not fk_column:
            return None
        return (child_table, fk_column)

    def _sql_lookup_edges(self, mapping_entries: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
        """Edges (parent_table, child_table) derived from lookup_of."""
        edges: List[Tuple[str, str]] = []
        for e in mapping_entries:
            parsed = self._parse_sql_lookup_of(e.get("lookup_of"))
            if not parsed:
                continue
            child_table, _ = parsed
            parent = (e.get("table") or "").strip()
            if not parent:
                continue
            if parent.lower() == child_table.lower():
                raise ValueError(
                    f"Invalid lookup_of {e.get('lookup_of')!r}: parent and child table must differ."
                )
            edges.append((parent, child_table))
        return edges

    def _tables_involved_in_sql_mapping(self, mapping_entries: List[Dict[str, Any]]) -> List[str]:
        tables = {e["table"] for e in mapping_entries if e.get("table")}
        for e in mapping_entries:
            parsed = self._parse_sql_lookup_of(e.get("lookup_of"))
            if parsed:
                tables.add(parsed[0])
        return list(tables)

    def _topological_sort_sql_tables(
        self, tables: List[str], edges: List[Tuple[str, str]]
    ) -> List[str]:
        """Order tables so parents (FK sources) appear before children."""
        table_set = set(tables)
        indegree: Dict[str, int] = {t: 0 for t in table_set}
        adj: Dict[str, List[str]] = defaultdict(list)
        for parent, child in edges:
            if parent not in table_set or child not in table_set:
                continue
            adj[parent].append(child)
            indegree[child] = indegree.get(child, 0) + 1
        q = deque(sorted([t for t in table_set if indegree[t] == 0]))
        order: List[str] = []
        while q:
            n = q.popleft()
            order.append(n)
            for v in adj.get(n, []):
                indegree[v] -= 1
                if indegree[v] == 0:
                    q.append(v)
        if len(order) != len(table_set):
            raise ValueError(
                "Circular lookup_of / table dependency detected; cannot determine insert order."
            )
        return order

    def _key_columns_for_sql_table(
        self, mapping_entries: List[Dict[str, Any]], table: str
    ) -> List[str]:
        seen: List[str] = []
        for e in mapping_entries:
            if e.get("table") != table:
                continue
            if not e.get("is_duplicate_check"):
                continue
            col = e.get("column")
            if col and col not in seen:
                seen.append(col)
        return seen

    @staticmethod
    def _extract_pk_from_row(row: Dict[str, Any], pk_column: Optional[str]) -> Any:
        if pk_column and pk_column in row:
            return row[pk_column]
        for id_col in ("id", "Id", "ID", "_id"):
            if id_col in row:
                return row[id_col]
        return None

    async def _insert_row_returning_pk(
        self,
        connector,
        quoted_table: str,
        row_values: Dict[str, Any],
        pk_column: str,
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]],
    ) -> Any:
        transformed = self._transform_row_values(row_values, column_types, connector_type)
        if not transformed:
            raise ValueError("Cannot INSERT empty row for lookup migration")
        ct = connector_type.lower()
        cols = list(transformed.keys())
        qcols = [self._quote_identifier(connector_type, c) for c in cols]
        column_list = ", ".join(qcols)
        placeholders = ", ".join(["%s"] * len(cols))
        params = tuple(transformed[c] for c in cols)
        qpk = self._quote_identifier(connector_type, pk_column)

        if ct in ("postgresql", "postgres"):
            sql = f"INSERT INTO {quoted_table} ({column_list}) VALUES ({placeholders}) RETURNING {qpk} AS _pk"
            rows = await connector.execute_query(sql, params)
            if not rows:
                raise RuntimeError("INSERT ... RETURNING produced no row")
            return self._extract_pk_from_row(rows[0], "_pk") or rows[0].get(pk_column)

        if ct in ("mysql", "mariadb"):
            sql = f"INSERT INTO {quoted_table} ({column_list}) VALUES ({placeholders})"
            await connector.execute_non_query(sql, params)
            lid = await connector.execute_query("SELECT LAST_INSERT_ID() AS _pk", None)
            if lid and lid[0].get("_pk") is not None:
                return lid[0]["_pk"]
            raise RuntimeError("MySQL LAST_INSERT_ID() unavailable after INSERT")

        if ct in ("mssql", "sqlserver"):
            sql = (
                f"INSERT INTO {quoted_table} ({column_list}) "
                f"OUTPUT INSERTED.{qpk} AS _pk VALUES ({placeholders})"
            )
            rows = await connector.execute_query(sql, params)
            if not rows:
                raise RuntimeError("MSSQL INSERT OUTPUT produced no row")
            return self._extract_pk_from_row(rows[0], "_pk") or rows[0].get(pk_column)

        raise ValueError(
            f"SQL lookup migration with RETURNING / identity is not implemented for connector {connector_type!r}. "
            "Supported: PostgreSQL, MySQL/MariaDB, SQL Server."
        )

    async def _ensure_parent_row_pk_for_lookup(
        self,
        connector,
        quoted_table: str,
        parent_row: Dict[str, Any],
        key_columns: List[str],
        pk_column: Optional[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]],
    ) -> Any:
        if not pk_column:
            raise ValueError("Parent table requires a primary key column in schema for lookup migration")
        transformed = self._transform_row_values(parent_row, column_types, connector_type)
        if key_columns:
            existing = await self._query_existing_records(
                connector,
                quoted_table,
                [transformed],
                key_columns,
                connector_type,
                column_types,
                pk_column,
            )
            if existing:
                pk = self._extract_pk_from_row(existing[0], pk_column)
                if pk is not None:
                    tr = self._transform_row_values(parent_row, column_types, connector_type)
                    tr[pk_column] = pk
                    pk_dt = (
                        (column_types or {}).get(pk_column, {}).get("data_type", "")
                        if column_types and pk_column
                        else ""
                    )
                    await self._update_batch(
                        connector,
                        quoted_table,
                        [tr],
                        key_columns,
                        connector_type,
                        column_types,
                        pk_column,
                        pk_dt or None,
                    )
                    return pk
        return await self._insert_row_returning_pk(
            connector, quoted_table, parent_row, pk_column, connector_type, column_types
        )

    @staticmethod
    def _sql_natural_key_tuple(
        row: Dict[str, Any], key_columns: List[str]
    ) -> Optional[Tuple[str, ...]]:
        """Stable tuple for deduplicating parent rows (matches duplicate-check semantics)."""
        if not key_columns:
            return None
        parts: List[str] = []
        for col in key_columns:
            v = row.get(col)
            if v is None:
                v = ""
            elif isinstance(v, str):
                v = v.strip()
            parts.append(str(v))
        if not all(parts):
            return None
        return tuple(parts)

    @staticmethod
    def _normalize_lookup_rows_union_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Pad rows to the same column set so batch UPDATE builds stable SQL."""
        if not rows:
            return []
        all_keys = sorted({k for r in rows for k in r.keys()})
        return [{k: r.get(k) for k in all_keys} for r in rows]

    @staticmethod
    def _merge_sql_lookup_row_dicts(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Merge multiple partial parent rows sharing the same natural key; later values win."""
        merged: Dict[str, Any] = {}
        for r in rows:
            for k, v in r.items():
                merged[k] = v
        return merged

    def _build_lookup_row_for_table(
        self,
        record: Dict[str, Any],
        table: str,
        mapping_entries: List[Dict[str, Any]],
        resolved_pks: Dict[str, Any],
        column_types_by_table: Dict[str, Dict[str, Dict[str, Any]]],
        connector_type: str,
    ) -> Dict[str, Any]:
        direct = [
            e
            for e in mapping_entries
            if e.get("table") == table and not (e.get("lookup_of") or "").strip()
        ]
        via_lookup_parent = [
            e
            for e in mapping_entries
            if e.get("table") == table and (e.get("lookup_of") or "").strip()
        ]
        row: Dict[str, Any] = {}
        if direct:
            row.update(
                self._build_row_values(
                    record, direct, column_types_by_table.get(table), connector_type
                )
            )
        if via_lookup_parent:
            row.update(
                self._build_row_values(
                    record, via_lookup_parent, column_types_by_table.get(table), connector_type
                )
            )
        for e in mapping_entries:
            parsed = self._parse_sql_lookup_of(e.get("lookup_of"))
            if not parsed:
                continue
            child_table, fk_col = parsed
            if child_table != table:
                continue
            pt = e.get("table")
            if pt not in resolved_pks:
                raise RuntimeError(
                    f"FK {fk_col} on {child_table} needs parent {pt} PK before row is built for {table}"
                )
            row[fk_col] = resolved_pks[pt]
        return row

    async def _batch_insert_lookup_parents_return_pks(
        self,
        connector,
        quoted_table: str,
        rows: List[Dict[str, Any]],
        pk_column: str,
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]],
        key_columns: List[str],
    ) -> List[Any]:
        """
        Insert many parent rows in one statement; return PKs in the same order as rows.
        MySQL: INSERT then re-SELECT by duplicate-key columns.
        """
        if not rows:
            return []
        ct = connector_type.lower()
        transformed_rows = [
            self._transform_row_values(r, column_types, connector_type) for r in rows
        ]
        all_cols = sorted({c for tr in transformed_rows for c in tr.keys()})
        if not all_cols:
            raise ValueError("batch parent insert: no columns")
        qcols = [self._quote_identifier(connector_type, c) for c in all_cols]
        column_list = ", ".join(qcols)
        qpk = self._quote_identifier(connector_type, pk_column)
        value_groups = []
        flat_params: List[Any] = []
        for tr in transformed_rows:
            ph = ", ".join(["%s"] * len(all_cols))
            value_groups.append(f"({ph})")
            flat_params.extend(tr.get(c) for c in all_cols)
        values_clause = ", ".join(value_groups)

        if ct in ("postgresql", "postgres"):
            sql = (
                f"INSERT INTO {quoted_table} ({column_list}) VALUES {values_clause} "
                f"RETURNING {qpk} AS _pk"
            )
            out = await connector.execute_query(sql, tuple(flat_params))
            if len(out) != len(rows):
                raise RuntimeError(
                    f"INSERT RETURNING row count mismatch: expected {len(rows)}, got {len(out)}"
                )
            return [self._extract_pk_from_row(r, "_pk") or r.get(pk_column) for r in out]

        if ct in ("mssql", "sqlserver"):
            sql = (
                f"INSERT INTO {quoted_table} ({column_list}) "
                f"OUTPUT INSERTED.{qpk} AS _pk "
                f"VALUES {values_clause}"
            )
            out = await connector.execute_query(sql, tuple(flat_params))
            if len(out) != len(rows):
                raise RuntimeError(
                    f"MSSQL INSERT OUTPUT row count mismatch: expected {len(rows)}, got {len(out)}"
                )
            return [self._extract_pk_from_row(r, "_pk") or r.get(pk_column) for r in out]

        if ct in ("mysql", "mariadb"):
            sql = f"INSERT INTO {quoted_table} ({column_list}) VALUES {values_clause}"
            await connector.execute_non_query(sql, tuple(flat_params))
            found = await self._query_existing_records(
                connector,
                quoted_table,
                transformed_rows,
                key_columns,
                connector_type,
                column_types,
                pk_column,
            )
            key_to_pk: Dict[Tuple[str, ...], Any] = {}
            for er in found:
                kt = self._sql_natural_key_tuple(er, key_columns)
                if kt:
                    pk = self._extract_pk_from_row(er, pk_column)
                    if pk is not None:
                        key_to_pk[kt] = pk
            ordered: List[Any] = []
            for tr in transformed_rows:
                kt = self._sql_natural_key_tuple(tr, key_columns)
                if kt is None or kt not in key_to_pk:
                    raise RuntimeError(
                        "MySQL batch parent insert: could not resolve PK for a row "
                        "(ensure duplicate-check columns match a UNIQUE key)."
                    )
                ordered.append(key_to_pk[kt])
            return ordered

        raise ValueError(f"Batch parent insert not supported for connector {connector_type!r}")

    async def _resolve_parent_pks_for_chunk_batch(
        self,
        connector,
        quoted_table: str,
        parent_rows_by_index: List[Optional[Dict[str, Any]]],
        key_columns: List[str],
        pk_column: Optional[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]],
        cancel_check,
    ) -> Tuple[List[Optional[Any]], int, int, Dict[int, str]]:
        """
        Deduplicate parents by natural key within the chunk, bulk SELECT existing,
        UPDATE existing parents with mapped non-key fields, batch INSERT missing,
        assign PK per source row index.
        Returns (pk_per_index, inserted_new_parent_rows, updated_existing_parent_rows,
                 parent_index_action: source row index -> "updated"|"created").
        """
        n = len(parent_rows_by_index)
        result: List[Optional[Any]] = [None] * n
        inserted_parent_rows = 0
        updated_parent_rows = 0
        parent_index_action: Dict[int, str] = {}
        if not pk_column:
            raise ValueError("Parent table requires a primary key column in schema for lookup migration")

        if not key_columns:
            for i in range(n):
                if cancel_check and await cancel_check():
                    from utils.migration_cancel import MigrationCancelledError

                    raise MigrationCancelledError("Migration cancelled by user")
                row = parent_rows_by_index[i]
                if not row:
                    continue
                result[i] = await self._ensure_parent_row_pk_for_lookup(
                    connector,
                    quoted_table,
                    row,
                    key_columns,
                    pk_column,
                    connector_type,
                    column_types,
                )
            return result, inserted_parent_rows, updated_parent_rows, parent_index_action

        key_to_indices: Dict[Tuple[str, ...], List[int]] = defaultdict(list)
        key_to_raw_rows: Dict[Tuple[str, ...], List[Dict[str, Any]]] = defaultdict(list)

        for i in range(n):
            row = parent_rows_by_index[i]
            if not row:
                continue
            tr = self._transform_row_values(row, column_types, connector_type)
            kt = self._sql_natural_key_tuple(tr, key_columns)
            if kt is None:
                if cancel_check and await cancel_check():
                    from utils.migration_cancel import MigrationCancelledError

                    raise MigrationCancelledError("Migration cancelled by user")
                result[i] = await self._ensure_parent_row_pk_for_lookup(
                    connector,
                    quoted_table,
                    row,
                    key_columns,
                    pk_column,
                    connector_type,
                    column_types,
                )
                continue
            key_to_indices[kt].append(i)
            key_to_raw_rows[kt].append(row)

        if not key_to_indices:
            return result, inserted_parent_rows, updated_parent_rows, parent_index_action

        unique_keys = sorted(key_to_indices.keys(), key=lambda k: key_to_indices[k][0])
        rep_rows: List[Dict[str, Any]] = []
        rep_key_order: List[Tuple[str, ...]] = []
        for kt in unique_keys:
            merged = self._merge_sql_lookup_row_dicts(key_to_raw_rows[kt])
            rep_rows.append(merged)
            rep_key_order.append(kt)

        if cancel_check and await cancel_check():
            from utils.migration_cancel import MigrationCancelledError

            raise MigrationCancelledError("Migration cancelled by user")

        existing_list = await self._query_existing_records(
            connector,
            quoted_table,
            rep_rows,
            key_columns,
            connector_type,
            column_types,
            pk_column,
        )
        key_to_pk: Dict[Tuple[str, ...], Any] = {}
        keys_pre_existing: Set[Tuple[str, ...]] = set()
        for er in existing_list:
            kt = self._sql_natural_key_tuple(er, key_columns)
            if kt:
                pk = self._extract_pk_from_row(er, pk_column)
                if pk is not None:
                    key_to_pk[kt] = pk
                    keys_pre_existing.add(kt)

        _migration_debug(f"[SQL lookup debug] parent batch: {len(rep_key_order)} unique natural keys, "
            f"{len(keys_pre_existing)} matched existing rows, "
            f"connector={connector_type!r} table={quoted_table!r}")

        update_kts = [kt for kt in rep_key_order if kt in keys_pre_existing]
        if update_kts and pk_column:
            update_rows_raw: List[Dict[str, Any]] = []
            kts_in_update_batch: List[Tuple[str, ...]] = []
            for kt in update_kts:
                merged = self._merge_sql_lookup_row_dicts(key_to_raw_rows[kt])
                tr = self._transform_row_values(merged, column_types, connector_type)
                pk = key_to_pk.get(kt)
                if pk is None:
                    continue
                tr[pk_column] = pk
                update_rows_raw.append(tr)
                kts_in_update_batch.append(kt)
            if update_rows_raw:
                norm = self._normalize_lookup_rows_union_columns(update_rows_raw)
                pk_dt = (
                    (column_types or {}).get(pk_column, {}).get("data_type", "")
                    if column_types and pk_column
                    else ""
                )
                for bs in range(0, len(norm), self.BATCH_SIZE):
                    if cancel_check and await cancel_check():
                        from utils.migration_cancel import MigrationCancelledError

                        raise MigrationCancelledError("Migration cancelled by user")
                    chunk = norm[bs : bs + self.BATCH_SIZE]
                    u = await self._update_batch(
                        connector,
                        quoted_table,
                        chunk,
                        key_columns,
                        connector_type,
                        column_types,
                        pk_column,
                        pk_dt or None,
                    )
                    updated_parent_rows += int(u or 0)
                for kt in kts_in_update_batch:
                    for idx in key_to_indices.get(kt, []):
                        parent_index_action[idx] = "updated"
                _migration_debug(f"[SQL lookup debug] parent UPDATE: {len(kts_in_update_batch)} natural key(s) -> "
                    f"{len({i for kt in kts_in_update_batch for i in key_to_indices.get(kt, [])})} source row(s)")

        missing_keys = [kt for kt in rep_key_order if kt not in key_to_pk]
        for batch_start in range(0, len(missing_keys), self.BATCH_SIZE):
            if cancel_check and await cancel_check():
                from utils.migration_cancel import MigrationCancelledError

                raise MigrationCancelledError("Migration cancelled by user")
            batch_keys = missing_keys[batch_start : batch_start + self.BATCH_SIZE]
            to_insert = [self._merge_sql_lookup_row_dicts(key_to_raw_rows[kt]) for kt in batch_keys]
            new_pks = await self._batch_insert_lookup_parents_return_pks(
                connector,
                quoted_table,
                to_insert,
                pk_column,
                connector_type,
                column_types,
                key_columns,
            )
            for kt, pk in zip(batch_keys, new_pks):
                key_to_pk[kt] = pk
            inserted_parent_rows += len(batch_keys)
            for kt in batch_keys:
                for idx in key_to_indices.get(kt, []):
                    parent_index_action[idx] = "created"
            _migration_debug(f"[SQL lookup debug] parent INSERT: {len(batch_keys)} new natural key(s) in this sub-batch")

        for kt in rep_key_order:
            pk = key_to_pk.get(kt)
            if pk is None:
                raise RuntimeError(f"Failed to resolve PK for parent natural key {kt!r}")
            for idx in key_to_indices[kt]:
                result[idx] = pk

        return result, inserted_parent_rows, updated_parent_rows, parent_index_action

    def _lookup_injected_fk_columns_for_child(
        self, mapping_entries: List[Dict[str, Any]], child_table: str
    ) -> List[str]:
        """FK column names on child that are filled via lookup_of (parent PK inject)."""
        out: List[str] = []
        ct_lower = (child_table or "").strip().lower()
        for e in mapping_entries:
            parsed = self._parse_sql_lookup_of(e.get("lookup_of"))
            if not parsed:
                continue
            ctable, fk_col = parsed
            if ctable.strip().lower() != ct_lower:
                continue
            col = (fk_col or "").strip()
            if col and col not in out:
                out.append(col)
        return out

    def _effective_child_duplicate_keys(
        self,
        mapping_entries: List[Dict[str, Any]],
        child_table: str,
        keys_from_mapping: List[str],
    ) -> List[str]:
        """
        Duplicate-check columns for child upsert: mapping is_duplicate_check columns plus
        lookup-injected FK columns (e.g. user_id) so we match existing rows on unique FK-only constraints.
        """
        seen: Set[str] = set()
        ordered: List[str] = []
        for c in keys_from_mapping:
            if c and c not in seen:
                seen.add(c)
                ordered.append(c)
        for c in self._lookup_injected_fk_columns_for_child(mapping_entries, child_table):
            if c and c not in seen:
                seen.add(c)
                ordered.append(c)
        return ordered

    def _lookup_child_duplicate_keys(
        self,
        mapping_entries: List[Dict[str, Any]],
        child_table: str,
        keys_from_mapping: List[str],
    ) -> List[str]:
        """
        Columns used to find existing child rows in lookup migrations.

        When the DB enforces uniqueness on a single lookup-injected FK (e.g. user_plans.user_id),
        matching only on mapping duplicate columns (plan_type, status, ...) misses rows that
        share user_id but differ on other fields, causing INSERT duplicate-key errors.

        Heuristic: if exactly one lookup-injected FK column exists on the child, use it alone
        as the duplicate key (typical 1:1 child row per parent). Otherwise use the union of
        mapping keys and injected FK columns.
        """
        fk_cols = self._lookup_injected_fk_columns_for_child(mapping_entries, child_table)
        if len(fk_cols) == 1:
            return fk_cols
        return self._effective_child_duplicate_keys(mapping_entries, child_table, keys_from_mapping)

    @staticmethod
    def _flatten_lookup_mapped_tables(per_table: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Single-level dict for migration UI: 'table.column' -> value."""
        flat: Dict[str, Any] = {}
        for tbl, row in (per_table or {}).items():
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                flat[f"{tbl}.{col}"] = val
        return flat

    @staticmethod
    def _aggregate_lookup_destination_status(
        per_table: Dict[str, str],
    ) -> Tuple[str, str]:
        """Overall status/message from per-table created/updated strings."""
        if not per_table:
            return "created", "Lookup migration (no table actions recorded)"
        if any(v == "updated" for v in per_table.values()):
            agg = "updated"
        else:
            agg = "created"
        parts = [f"{tbl}: {st}" for tbl, st in sorted(per_table.items())]
        return agg, "; ".join(parts)

    async def _migrate_sql_with_lookup_relationships(
        self,
        data: List[Dict[str, Any]],
        mapping_entries: List[Dict[str, Any]],
        connector_instance,
        connector_type: str,
        connector_id: str,
        table_name: str,
        user_id: str,
        operation: str,
        batch_id: Optional[str],
        document_type: Optional[str],
        start_index: int,
        destination_name: Optional[str],
        migration_job_id: Optional[str],
        job_store,
    ) -> Dict[str, Any]:
        """
        Multi-table migration: map natural keys to parent table(s), resolve PK, inject into child FK
        columns named by lookup_of ("child_table.fk_column"), then insert rows in dependency order.
        """
        if (operation or "append").lower() == "overwrite":
            raise ValueError(
                "overwrite operation is not supported when lookup_of is used; use append/upsert-style keys on parents."
            )

        edges = self._sql_lookup_edges(mapping_entries)
        tables = self._tables_involved_in_sql_mapping(mapping_entries)
        if len(tables) < 2:
            raise ValueError(
                "lookup_of requires mappings to at least two tables (parent columns and child.fk_column)."
            )
        topo = self._topological_sort_sql_tables(tables, edges)

        column_types_by_table: Dict[str, Dict[str, Dict[str, Any]]] = {}
        pk_by_table: Dict[str, Optional[str]] = {}
        quoted_by_table: Dict[str, str] = {}
        for t in tables:
            schema = await connector_instance.get_table_schema(t)
            cols: Dict[str, Dict[str, Any]] = {}
            if schema and "columns" in schema:
                for col in schema["columns"]:
                    cols[col["column_name"]] = {
                        "data_type": col.get("data_type", ""),
                        "is_nullable": col.get("is_nullable", True),
                        "is_primary_key": col.get("is_primary_key", False),
                    }
            column_types_by_table[t] = cols
            pk_info = self._get_primary_key_column(cols)
            pk_by_table[t] = pk_info[0] if pk_info else None
            quoted_by_table[t] = self._quote_identifier(connector_type, t)

        effective_table_name = topo[-1] if topo else table_name

        if not batch_id:
            batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                batch_name=f"SQL_lookup_{effective_table_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                document_type=document_type or "SQL Migration (lookup)",
                total_records=len(data),
                mapping_config={
                    "connector_id": connector_id,
                    "table_name": effective_table_name,
                    "destination_name": destination_name,
                    "lookup_migration": True,
                    "tables": tables,
                    "lookup_tables_order": topo,
                },
                destination_type="sql",
            )

        cancel_check = None
        if migration_job_id and job_store:
            from utils.migration_cancel import make_cancel_check

            cancel_check = make_cancel_check(migration_job_id, job_store)

        insert_count = 0
        update_count = 0
        error_count = 0
        success_records = 0
        formatted_results: List[Dict[str, Dict[str, Any]]] = []
        record_results: List[Dict[str, Any]] = []

        n = len(data)
        if cancel_check and await cancel_check():
            from utils.migration_cancel import MigrationCancelledError

            raise MigrationCancelledError("Migration cancelled by user")

        record_failed = [False] * n
        record_errors = ["" for _ in range(n)]
        resolved_by_record: List[Dict[str, Any]] = [{} for _ in range(n)]
        per_record_rows: List[Dict[str, Dict[str, Any]]] = [{} for _ in range(n)]
        per_index_table_status: Dict[int, Dict[str, str]] = defaultdict(dict)

        for t in topo:
            is_parent_for_lookup = any(
                (e.get("table") == t and (e.get("lookup_of") or "").strip())
                for e in mapping_entries
            )
            parent_slice: List[Optional[Dict[str, Any]]] = []
            for i, record in enumerate(data):
                if record_failed[i]:
                    parent_slice.append(None)
                    continue
                try:
                    row = self._build_lookup_row_for_table(
                        record,
                        t,
                        mapping_entries,
                        resolved_by_record[i],
                        column_types_by_table,
                        connector_type,
                    )
                    parent_slice.append(row if row else None)
                    if row:
                        per_record_rows[i][t] = dict(row)
                except Exception as exc:
                    record_failed[i] = True
                    record_errors[i] = str(exc)
                    parent_slice.append(None)

            if not any(x for x in parent_slice if x):
                continue

            qtbl = quoted_by_table[t]
            ctmap = column_types_by_table.get(t)
            pk_col = pk_by_table.get(t)
            keys = self._key_columns_for_sql_table(mapping_entries, t)

            if is_parent_for_lookup:
                try:
                    pks, parent_ins, parent_upd, parent_index_action = (
                        await self._resolve_parent_pks_for_chunk_batch(
                            connector_instance,
                            qtbl,
                            parent_slice,
                            keys,
                            pk_col,
                            connector_type,
                            ctmap,
                            cancel_check,
                        )
                    )
                    insert_count += parent_ins
                    update_count += parent_upd
                    for i, act in (parent_index_action or {}).items():
                        if act in ("created", "updated"):
                            per_index_table_status[i][t] = act
                except Exception as exc:
                    _migration_debug(f"⚠️ [SQL lookup] batch parent resolve failed for {t}, falling back per row: {exc}")
                    pks = [None] * n
                    for i in range(n):
                        if record_failed[i] or not parent_slice[i]:
                            continue
                        try:
                            pks[i] = await self._ensure_parent_row_pk_for_lookup(
                                connector_instance,
                                qtbl,
                                parent_slice[i],
                                keys,
                                pk_col,
                                connector_type,
                                ctmap,
                            )
                        except Exception as e2:
                            record_failed[i] = True
                            record_errors[i] = str(e2)
                for i in range(n):
                    if pks[i] is not None:
                        resolved_by_record[i][t] = pks[i]
                    elif is_parent_for_lookup and parent_slice[i]:
                        record_failed[i] = True
                        if not record_errors[i]:
                            record_errors[i] = (
                                f"Could not resolve primary key for parent table {t!r}"
                            )
            else:
                insert_indices = [i for i in range(n) if not record_failed[i] and parent_slice[i]]
                if not insert_indices:
                    continue
                child_keys = self._lookup_child_duplicate_keys(mapping_entries, t, keys)
                eff_union = self._effective_child_duplicate_keys(mapping_entries, t, keys)
                if child_keys != keys:
                    _migration_debug(f"[SQL lookup debug] child table {t!r}: using duplicate keys "
                        f"{child_keys!r} (mapping keys were {keys!r}; union would be {eff_union!r})")
                eff_op = self._determine_operation(operation, child_keys)
                pk_tuple = self._get_primary_key_column(ctmap or {})
                pk_dtype_child = pk_tuple[1] if pk_tuple else None
                for batch_start in range(0, len(insert_indices), self.BATCH_SIZE):
                    if cancel_check and await cancel_check():
                        from utils.migration_cancel import MigrationCancelledError

                        raise MigrationCancelledError("Migration cancelled by user")
                    batch_idx = insert_indices[batch_start : batch_start + self.BATCH_SIZE]
                    batch_rows = [parent_slice[i] for i in batch_idx]
                    transformed = [
                        self._transform_row_values(r, ctmap, connector_type) for r in batch_rows
                    ]
                    if eff_op != "upsert" or not child_keys or not pk_col:
                        try:
                            inserted = await self._insert_batch(
                                connector_instance,
                                qtbl,
                                batch_rows,
                                connector_type,
                                ctmap,
                            )
                            insert_count += inserted
                            for i in batch_idx:
                                per_index_table_status[i][t] = "created"
                        except Exception as exc:
                            _migration_debug(f"⚠️ [SQL lookup] batch insert failed for {t}, falling back per row: {exc}")
                            for i in batch_idx:
                                if record_failed[i]:
                                    continue
                                try:
                                    tr = self._transform_row_values(
                                        parent_slice[i], ctmap, connector_type
                                    )
                                    await self._insert_record(
                                        connector_instance,
                                        qtbl,
                                        tr,
                                        connector_type,
                                        ctmap,
                                    )
                                    insert_count += 1
                                    per_index_table_status[i][t] = "created"
                                except Exception as e2:
                                    record_failed[i] = True
                                    record_errors[i] = str(e2)
                    else:
                        try:
                            existing = await self._query_existing_records(
                                connector_instance,
                                qtbl,
                                transformed,
                                child_keys,
                                connector_type,
                                ctmap,
                                pk_col,
                            )
                            dup_map, full_map = self._build_id_mapping_from_query_results(
                                existing, child_keys, ctmap, pk_col
                            )
                            rec_w_idx = [(j, transformed[j]) for j in range(len(transformed))]
                            to_up, to_ins = self._split_records_for_bulk_operations_with_indices(
                                rec_w_idx, child_keys, dup_map, full_map, pk_col
                            )
                            _migration_debug(f"[SQL lookup debug] child {t!r}: batch {len(batch_idx)} row(s), "
                                f"existing_matches={len(dup_map)}, to_update={len(to_up)}, "
                                f"to_insert={len(to_ins)}, duplicate_keys={child_keys!r}")
                            if to_up:
                                up_rows = [row for _, row in to_up]
                                up_norm = self._normalize_lookup_rows_union_columns(up_rows)
                                for ubs in range(0, len(up_norm), self.BATCH_SIZE):
                                    if cancel_check and await cancel_check():
                                        from utils.migration_cancel import MigrationCancelledError

                                        raise MigrationCancelledError(
                                            "Migration cancelled by user"
                                        )
                                    uch = up_norm[ubs : ubs + self.BATCH_SIZE]
                                    ucnt = await self._update_batch(
                                        connector_instance,
                                        qtbl,
                                        uch,
                                        child_keys,
                                        connector_type,
                                        ctmap,
                                        pk_col,
                                        pk_dtype_child,
                                    )
                                    update_count += int(ucnt or 0)
                            for j, _ in to_up:
                                per_index_table_status[batch_idx[j]][t] = "updated"
                            if to_ins:
                                ins_raw = [batch_rows[j] for j, _ in to_ins]
                                try:
                                    insert_count += await self._insert_batch(
                                        connector_instance,
                                        qtbl,
                                        ins_raw,
                                        connector_type,
                                        ctmap,
                                    )
                                    for j, _ in to_ins:
                                        per_index_table_status[batch_idx[j]][t] = "created"
                                except Exception as exc_ins:
                                    _migration_debug(f"⚠️ [SQL lookup] child batch insert failed for {t}: {exc_ins}")
                                    for j, _ in to_ins:
                                        i = batch_idx[j]
                                        if record_failed[i]:
                                            continue
                                        try:
                                            row_u = {
                                                k: v
                                                for k, v in parent_slice[i].items()
                                                if (pk_col and k.lower() != pk_col.lower())
                                            }
                                            res = await self._upsert_record(
                                                connector_instance,
                                                qtbl,
                                                row_u,
                                                child_keys,
                                                connector_type,
                                                ctmap,
                                            )
                                            if res == "updated":
                                                update_count += 1
                                                per_index_table_status[i][t] = "updated"
                                            else:
                                                insert_count += 1
                                                per_index_table_status[i][t] = "created"
                                        except Exception as e2:
                                            record_failed[i] = True
                                            record_errors[i] = str(e2)
                        except Exception as exc:
                            _migration_debug(f"⚠️ [SQL lookup] child upsert batch failed for {t}, falling back per row: {exc}")
                            for j, i in enumerate(batch_idx):
                                if record_failed[i]:
                                    continue
                                try:
                                    row_u = {
                                        k: v
                                        for k, v in parent_slice[i].items()
                                        if (pk_col and k.lower() != pk_col.lower())
                                    }
                                    res = await self._upsert_record(
                                        connector_instance,
                                        qtbl,
                                        row_u,
                                        child_keys,
                                        connector_type,
                                        ctmap,
                                    )
                                    if res == "updated":
                                        update_count += 1
                                        per_index_table_status[i][t] = "updated"
                                    else:
                                        insert_count += 1
                                        per_index_table_status[i][t] = "created"
                                except Exception as e2:
                                    record_failed[i] = True
                                    record_errors[i] = str(e2)

        for idx, record in enumerate(data):
            if record_failed[idx]:
                error_count += 1
                err_msg = record_errors[idx] or "Migration failed"
                _migration_record_error(f"[SQL lookup migration] record {start_index + idx + 1}: {err_msg}")
                formatted_results.append(
                    {
                        effective_table_name: {
                            "status": "error",
                            "message": err_msg,
                            "record_index": start_index + idx + 1,
                        }
                    }
                )
                record_results.append(
                    {
                        "record_index": start_index + idx,
                        "source_record": record,
                        "mapped_row": {},
                        "status": "error",
                        "message": err_msg,
                    }
                )
            else:
                success_records += 1
                tbl_status: Dict[str, str] = dict(per_index_table_status[idx])
                for tbl in per_record_rows[idx]:
                    tbl_status.setdefault(tbl, "created")
                agg_status, agg_msg = self._aggregate_lookup_destination_status(tbl_status)
                flat_mapped = self._flatten_lookup_mapped_tables(per_record_rows[idx])
                dest_by_table = {
                    tbl: {
                        "status": st,
                        "message": f"Table {tbl}: row {st} (lookup path, batched)",
                    }
                    for tbl, st in tbl_status.items()
                }
                fr_entry: Dict[str, Dict[str, Any]] = {}
                order = {name: i for i, name in enumerate(topo)}
                for tbl in sorted(tbl_status.keys(), key=lambda x: order.get(x, 999)):
                    st = tbl_status[tbl]
                    fr_entry[tbl] = {
                        "status": "updated" if st == "updated" else "created",
                        "message": f"Table {tbl}: {st} (lookup relationship path, batched)",
                        "record_index": start_index + idx + 1,
                    }
                if not fr_entry:
                    fr_entry = {
                        effective_table_name: {
                            "status": agg_status,
                            "message": agg_msg,
                            "record_index": start_index + idx + 1,
                        }
                    }
                formatted_results.append(fr_entry)
                record_results.append(
                    {
                        "record_index": start_index + idx,
                        "source_record": record,
                        "mapped_row": flat_mapped,
                        "status": agg_status,
                        "message": agg_msg,
                        "destination_by_table": dest_by_table,
                    }
                )

        processed_records = success_records
        await MigrationRecordsService.update_batch_status(
            batch_id=batch_id,
            status="completed",
            successful_count=processed_records,
            failed_count=error_count,
        )
        await self._track_migration_records(
            batch_id=batch_id,
            user_id=user_id,
            table_name=effective_table_name,
            connector_id=connector_id,
            record_results=record_results,
            destination_name=destination_name,
        )
        if processed_records:
            await get_entitlements().track_successful_migration(user_id, processed_records)

        return {
            "status": "success",
            "batch_id": batch_id,
            "records_migrated": processed_records,
            "operation": "lookup_relationship",
            "rows_inserted": insert_count,
            "rows_updated": update_count,
            "connector_id": connector_id,
            "table_name": effective_table_name,
            "destination_name": destination_name,
            "formatted_results": formatted_results,
            "result": {"formatted_results": formatted_results},
        }

    def _prepare_mapping_entries(
        self,
        mapping: Dict[str, Dict[str, Any]],
        default_table: str,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Normalize mapping dict into ordered entries and extract key columns.
        Handles both simple mappings (dict) and array mappings (list) for split fields.
        """
        entries: List[Dict[str, Any]] = []
        key_columns: List[str] = []
        for field_name, config in mapping.items():
            # Handle array mappings (split fields)
            if isinstance(config, list):
                # Array mapping - process each item in the array
                for idx, item in enumerate(config):
                    if not isinstance(item, dict):
                        logger.debug(f"⚠️ Skipping array item {idx} for {field_name}: expected dict, got {type(item)}")
                        continue
                    table = (
                        item.get("table")
                        or item.get("object")
                        or item.get("sheet")
                        or default_table
                    )
                    column = item.get("column") or item.get("field")
                    if not table or not column:
                        continue
                    lookup_raw = item.get("lookup_of")
                    entry = {
                        "field": field_name,
                        "table": table,
                        "column": column,
                        "is_duplicate_check": item.get("is_duplicate_check", False),
                    }
                    if lookup_raw:
                        from utils.sql_mapping_sanitize import should_drop_sql_lookup_of

                        lo = str(lookup_raw).strip()
                        drop, reason = should_drop_sql_lookup_of(
                            table, lo, default_table, tables_structure=None
                        )
                        if drop:
                            _migration_debug(f"[SQL_MIGRATE_DEBUG] skip lookup_of field={field_name} "
                                f"table={table!r} lookup_of={lo!r} reason={reason}")
                        else:
                            entry["lookup_of"] = lo
                    entries.append(entry)
                    if entry["is_duplicate_check"]:
                        key_columns.append(column)
            elif isinstance(config, dict):
                # Simple mapping (one-to-one)
                table = (
                    config.get("table")
                    or config.get("object")
                    or config.get("sheet")
                    or default_table
                )
                column = config.get("column") or config.get("field")
                if not table or not column:
                    continue
                lookup_raw = config.get("lookup_of")
                entry = {
                    "field": field_name,
                    "table": table,
                    "column": column,
                    "is_duplicate_check": config.get("is_duplicate_check", False),
                }
                if lookup_raw:
                    from utils.sql_mapping_sanitize import should_drop_sql_lookup_of

                    lo = str(lookup_raw).strip()
                    drop, reason = should_drop_sql_lookup_of(
                        table, lo, default_table, tables_structure=None
                    )
                    if drop:
                        _migration_debug(f"[SQL_MIGRATE_DEBUG] skip lookup_of field={field_name} "
                            f"table={table!r} lookup_of={lo!r} reason={reason}")
                    else:
                        entry["lookup_of"] = lo
                entries.append(entry)
                if entry["is_duplicate_check"]:
                    key_columns.append(column)
            else:
                logger.debug(f"⚠️ Skipping mapping for {field_name}: expected dict or list, got {type(config)}")
                continue

        tables = {entry["table"] for entry in entries}
        has_lookup = self._mapping_entries_use_sql_lookup_of(entries)
        for e in entries:
            parsed = self._parse_sql_lookup_of(e.get("lookup_of"))
            if parsed:
                tables.add(parsed[0])

        if len(tables) > 1 and not has_lookup:
            raise ValueError(
                "Multiple destination tables detected. SQL migration supports multiple tables only when "
                "lookup_of is set on parent-field mappings (see flow mapping editor)."
            )
        if has_lookup and len(tables) < 2:
            raise ValueError(
                "lookup_of requires at least two tables: parent columns in mapping and child_table.fk_column in lookup_of."
            )

        return entries, key_columns

    def _build_row_values(
        self,
        record: Dict[str, Any],
        mapping_entries: List[Dict[str, Any]],
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
        connector_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Build row values from record and mapping entries, with optional type conversion.
        
        Args:
            record: Source data record
            mapping_entries: List of mapping entries (field -> column mappings)
            column_types: Optional dict mapping column_name -> {data_type, is_nullable}
            connector_type: Optional database connector type for type conversion
        """
        row: Dict[str, Any] = {}
        for entry in mapping_entries:
            value = _get_source_value_for_mapping(record, entry["field"])
            column_name = entry["column"]
            if (
                column_name
                and str(column_name).lower() == "email"
                and (value is None or (isinstance(value, str) and not str(value).strip()))
            ):
                alt = _fallback_value_for_email_column(record)
                if alt is not None:
                    value = alt
            
            # Apply type conversion if schema is available
            if column_types and connector_type and column_name in column_types:
                col_info = column_types[column_name]
                column_type = col_info.get('data_type', '')
                is_nullable = col_info.get('is_nullable', True)
                
                try:
                    value = convert_value_for_database(
                        value=value,
                        column_type=column_type,
                        connector_type=connector_type,
                        is_nullable=is_nullable,
                        column_name=column_name
                    )
                except Exception as e:
                    # Log error but continue with original value
                    logger.debug(f"⚠️ Type conversion error for column {column_name}: {e}, using original value")
                    # If conversion fails, use None for empty strings in timestamp/date columns
                    if (value == '' or value is None) and any(t in column_type.lower() for t in ['timestamp', 'date', 'time']):
                        value = None
            
            if column_name in row and _value_is_non_empty(row[column_name]) and not _value_is_non_empty(value):
                continue
            row[column_name] = value
        return row

    async def _insert_record(
        self,
        connector,
        quoted_table_name: str,
        row: Dict[str, Any],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Insert a single record (kept for backward compatibility and upsert operations)"""
        # Transform row values based on column types
        transformed_row = self._transform_row_values(row, column_types, connector_type)
        
        columns = list(transformed_row.keys())
        placeholders = ", ".join(["%s"] * len(columns))
        column_list = ", ".join(self._quote_identifier(connector_type, col) for col in columns)
        query = f"INSERT INTO {quoted_table_name} ({column_list}) VALUES ({placeholders})"
        params = tuple(transformed_row[col] for col in columns)
        
        await connector.execute_non_query(query, params)
    
    async def _insert_batch(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> int:
        """
        Insert multiple records in a single batch INSERT statement.
        
        Args:
            connector: Database connector instance
            quoted_table_name: Quoted table name
            rows: List of row dictionaries (column sets may differ; union of keys is used)
            connector_type: Database connector type
            column_types: Optional dict mapping column_name -> {data_type, is_nullable}
        
        Returns:
            Number of rows inserted
        """
        if not rows:
            return 0
        
        # Transform all rows based on column types
        transformed_rows = []
        for row in rows:
            transformed_row = self._transform_row_values(row, column_types, connector_type)
            transformed_rows.append(transformed_row)
        
        # Union of keys across rows so VALUES align when rows differ (sparse transforms, etc.)
        columns_set: Set[str] = set()
        for row in transformed_rows:
            columns_set.update(row.keys())
        columns = sorted(columns_set)
        column_list = ", ".join(self._quote_identifier(connector_type, col) for col in columns)
        
        # Get SQL casts for columns that need explicit casting
        column_casts = {}
        for col in columns:
            cast = self._get_sql_cast_for_column(col, column_types, connector_type)
            if cast:
                column_casts[col] = cast
        
        # Build multi-row VALUES clause: (val1, val2, ...), (val3, val4, ...), ...
        value_placeholders = []
        all_params = []
        
        for row in transformed_rows:
            # Build placeholders with casts if needed
            placeholders = []
            for col in columns:
                if col in column_casts:
                    placeholders.append(column_casts[col].replace('%s', '%s'))
                else:
                    placeholders.append('%s')
            
            row_values = [row.get(col) for col in columns]
            value_placeholders.append(f"({', '.join(placeholders)})")
            all_params.extend(row_values)
        
        # Build final query: INSERT INTO table (cols) VALUES (row1), (row2), ...
        values_clause = ", ".join(value_placeholders)
        query = f"INSERT INTO {quoted_table_name} ({column_list}) VALUES {values_clause}"
        
        # Execute batch insert
        await connector.execute_non_query(query, tuple(all_params))
        
        return len(transformed_rows)
    
    async def _upsert_batch(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, List[int]]:
        """
        Insert or update multiple records in a single batch upsert statement.
        
        Args:
            connector: Database connector instance
            quoted_table_name: Quoted table name
            rows: List of row dictionaries (all must have same columns)
            key_columns: List of column names that form the unique key
            connector_type: Database connector type
            column_types: Optional dict mapping column_name -> {data_type, is_nullable}
        
        Returns:
            Dict with 'inserted_indices' and 'updated_indices' lists
            Note: For some databases, we may not be able to distinguish, so all indices may be in 'inserted_indices'
        """
        if not rows:
            return {"inserted_indices": [], "updated_indices": []}
        
        non_key_columns = [col for col in rows[0].keys() if col not in key_columns]
        
        # For MySQL/MariaDB, use ON DUPLICATE KEY UPDATE
        if connector_type.lower() in ['mysql', 'mariadb']:
            return await self._upsert_batch_mysql(
                connector, quoted_table_name, rows, key_columns, non_key_columns, connector_type
            )
        
        # For PostgreSQL, use ON CONFLICT DO UPDATE
        if connector_type.lower() in ['postgresql', 'postgres']:
            return await self._upsert_batch_postgresql(
                connector, quoted_table_name, rows, key_columns, non_key_columns, connector_type, column_types
            )
        
        # For MSSQL, use MERGE (with sub-batching for 2100 parameter limit)
        if connector_type.lower() in ['mssql', 'sqlserver']:
            return await self._upsert_batch_mssql(
                connector, quoted_table_name, rows, key_columns, non_key_columns, connector_type
            )
        
        # For other databases, fall back to individual upserts
        raise NotImplementedError(f"Batch upsert not implemented for {connector_type}")
    
    async def _upsert_batch_mysql(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        key_columns: List[str],
        non_key_columns: List[str],
        connector_type: str,
    ) -> Dict[str, List[int]]:
        """
        Batch upsert for MySQL/MariaDB using ON DUPLICATE KEY UPDATE.
        Note: MySQL doesn't provide per-row insert/update status in batch operations,
        so we'll mark all as 'upserted' and query afterward if needed.
        """
        all_columns = list(rows[0].keys())
        column_list = ", ".join(self._quote_identifier(connector_type, col) for col in all_columns)
        
        # Build multi-row VALUES clause
        value_placeholders = []
        all_params = []
        
        for row in rows:
            row_values = [row.get(col) for col in all_columns]
            placeholders = ", ".join(["%s"] * len(all_columns))
            value_placeholders.append(f"({placeholders})")
            all_params.extend(row_values)
        
        # Build ON DUPLICATE KEY UPDATE clause
        if non_key_columns:
            update_clause = ", ".join([
                f"{self._quote_identifier(connector_type, col)} = VALUES({self._quote_identifier(connector_type, col)})"
                for col in non_key_columns
            ])
        else:
            # No non-key columns - use a no-op update
            first_key_col = key_columns[0] if key_columns else all_columns[0]
            update_clause = f"{self._quote_identifier(connector_type, first_key_col)} = VALUES({self._quote_identifier(connector_type, first_key_col)})"
        
        values_clause = ", ".join(value_placeholders)
        query = f"INSERT INTO {quoted_table_name} ({column_list}) VALUES {values_clause} ON DUPLICATE KEY UPDATE {update_clause}"
        
        # Execute batch upsert
        await connector.execute_non_query(query, tuple(all_params))
        
        # MySQL doesn't provide per-row status in batch operations
        # We'll return all indices as 'inserted' (the calling code can query if needed)
        # For now, we'll assume all were upserted successfully
        return {
            "inserted_indices": list(range(len(rows))),  # All indices (we can't distinguish)
            "updated_indices": []  # Empty - we can't tell which were updates
        }
    
    async def _upsert_batch_postgresql(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        key_columns: List[str],
        non_key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, List[int]]:
        """
        Batch upsert for PostgreSQL using ON CONFLICT DO UPDATE.
        Falls back to batch UPDATE-then-INSERT if no unique constraint exists.
        """
        all_columns = list(rows[0].keys())
        column_list = ", ".join(self._quote_identifier(connector_type, col) for col in all_columns)
        
        # Build multi-row VALUES clause
        value_placeholders = []
        all_params = []
        
        for row in rows:
            row_values = [row.get(col) for col in all_columns]
            placeholders = ", ".join(["%s"] * len(all_columns))
            value_placeholders.append(f"({placeholders})")
            all_params.extend(row_values)
        
        # Build ON CONFLICT clause - need to identify the unique constraint
        # For now, we'll use the key_columns as the conflict target
        conflict_target = ", ".join(self._quote_identifier(connector_type, col) for col in key_columns)
        
        # Build DO UPDATE clause
        if non_key_columns:
            update_clause = ", ".join([
                f"{self._quote_identifier(connector_type, col)} = EXCLUDED.{self._quote_identifier(connector_type, col)}"
                for col in non_key_columns
            ])
        else:
            # No non-key columns - use a no-op update
            first_key_col = key_columns[0] if key_columns else all_columns[0]
            update_clause = f"{self._quote_identifier(connector_type, first_key_col)} = EXCLUDED.{self._quote_identifier(connector_type, first_key_col)}"
        
        values_clause = ", ".join(value_placeholders)
        query = f"INSERT INTO {quoted_table_name} ({column_list}) VALUES {values_clause} ON CONFLICT ({conflict_target}) DO UPDATE SET {update_clause}"
        
        try:
            # Try ON CONFLICT first (requires unique constraint)
            await connector.execute_non_query(query, tuple(all_params))
            
            # PostgreSQL doesn't provide per-row status in batch operations either
            # We'll return all indices as 'inserted' (the calling code can query if needed)
            return {
                "inserted_indices": list(range(len(rows))),  # All indices (we can't distinguish)
                "updated_indices": []  # Empty - we can't tell which were updates
            }
        except Exception as e:
            error_msg = str(e).lower()
            # Check if error is about missing unique constraint
            if "no unique or exclusion constraint" in error_msg or "there is no unique constraint" in error_msg:
                # Re-raise with a clearer message so the caller knows to fall back to individual upserts
                # (The fallback code will handle individual upserts using UPDATE-then-INSERT)
                raise ValueError(
                    f"PostgreSQL ON CONFLICT requires a unique constraint on key columns ({', '.join(key_columns)}). "
                    f"Falling back to individual upserts. Original error: {e}"
                )
            else:
                # Re-raise other errors
                raise

    async def _upsert_batch_mssql(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        key_columns: List[str],
        non_key_columns: List[str],
        connector_type: str,
    ) -> Dict[str, List[int]]:
        """
        Batch upsert for MSSQL using MERGE. Sub-batches to stay under 2100 parameter limit.
        """
        all_columns = list(rows[0].keys())
        num_cols = len(all_columns)
        # MSSQL limit 2100 params per statement
        max_rows_per_batch = max(1, 2100 // num_cols)
        inserted_indices = []
        updated_indices = []

        for chunk_start in range(0, len(rows), max_rows_per_batch):
            chunk = rows[chunk_start:chunk_start + max_rows_per_batch]
            chunk_indices = list(range(chunk_start, chunk_start + len(chunk)))

            column_list = ", ".join(self._quote_identifier(connector_type, col) for col in all_columns)
            value_placeholders = []
            all_params = []
            for row in chunk:
                row_vals = [row.get(col) for col in all_columns]
                placeholders = ", ".join(["%s"] * num_cols)
                value_placeholders.append(f"({placeholders})")
                all_params.extend(row_vals)

            values_clause = ", ".join(value_placeholders)
            source_alias = ", ".join(self._quote_identifier(connector_type, col) for col in all_columns)
            on_parts = " AND ".join(
                f"t.{self._quote_identifier(connector_type, k)} = s.{self._quote_identifier(connector_type, k)}"
                for k in key_columns
            )
            if non_key_columns:
                update_set = ", ".join(
                    f"t.{self._quote_identifier(connector_type, c)} = s.{self._quote_identifier(connector_type, c)}"
                    for c in non_key_columns
                )
            else:
                first_key = key_columns[0] if key_columns else all_columns[0]
                update_set = f"t.{self._quote_identifier(connector_type, first_key)} = s.{self._quote_identifier(connector_type, first_key)}"
            insert_cols = ", ".join(self._quote_identifier(connector_type, c) for c in all_columns)
            insert_vals = ", ".join(f"s.{self._quote_identifier(connector_type, c)}" for c in all_columns)

            merge_sql = (
                f"MERGE INTO {quoted_table_name} AS t "
                f"USING (VALUES {values_clause}) AS s ({source_alias}) "
                f"ON {on_parts} "
                f"WHEN MATCHED THEN UPDATE SET {update_set} "
                f"WHEN NOT MATCHED BY TARGET THEN INSERT ({insert_cols}) VALUES ({insert_vals});"
            )
            await connector.execute_non_query(merge_sql, tuple(all_params))
            # MERGE doesn't report per-row status; treat all as upserted
            inserted_indices.extend(chunk_indices)

        return {"inserted_indices": inserted_indices, "updated_indices": []}

    def _build_where_clause_with_trim(
        self,
        key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> str:
        """
        Build WHERE clause with TRIM() for string columns to handle whitespace differences.
        This ensures we can match existing records even if they have leading/trailing whitespace.
        Note: This is only used for PostgreSQL and other databases. MySQL uses ON DUPLICATE KEY UPDATE.
        """
        conditions = []
        for col in key_columns:
            quoted_col = self._quote_identifier(connector_type, col)
            
            # Check if column is a string type - use TRIM() for string columns
            is_string_type = False
            if column_types and col in column_types:
                data_type = column_types[col].get('data_type', '').lower()
                is_string_type = any(t in data_type for t in ['char', 'varchar', 'text', 'string'])
            
            # Use TRIM() for string columns to handle whitespace differences
            # (MySQL uses ON DUPLICATE KEY UPDATE, so this is mainly for PostgreSQL)
            if is_string_type and connector_type.lower() in ['postgresql', 'postgres', 'mssql', 'sqlserver']:
                # MSSQL 2017+ has TRIM(); older versions could use LTRIM(RTRIM(...))
                condition = f"TRIM({quoted_col}) = TRIM(%s)"
                conditions.append(condition)
            else:
                # For non-string types or unknown types, use exact match
                condition = f"{quoted_col} = %s"
                conditions.append(condition)
        
        return " AND ".join(conditions)

    async def _upsert_record(
        self,
        connector,
        quoted_table_name: str,
        row: Dict[str, Any],
        key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        # Transform row values based on column types
        transformed_row = self._transform_row_values(row, column_types, connector_type)
        
        non_key_columns = [col for col in transformed_row.keys() if col not in key_columns]
        
        # For MySQL/MariaDB, use ON DUPLICATE KEY UPDATE (most reliable)
        if connector_type.lower() in ['mysql', 'mariadb']:
            return await self._upsert_with_on_duplicate_key_update(
                connector, quoted_table_name, transformed_row, key_columns, non_key_columns, connector_type, column_types
            )
        
        # For PostgreSQL, MSSQL, and other databases, use UPDATE-then-INSERT logic
        if not non_key_columns:
            # Only key columns - check if exists, then insert if not
            where_clause = self._build_where_clause_with_trim(key_columns, connector_type, column_types)
            if connector_type.lower() in ['mssql', 'sqlserver']:
                select_query = f"SELECT TOP 1 1 FROM {quoted_table_name} WHERE {where_clause}"
            else:
                select_query = f"SELECT 1 FROM {quoted_table_name} WHERE {where_clause} LIMIT 1"
            select_params = tuple(transformed_row[col] for col in key_columns)
            
            existing = await connector.execute_query(select_query, select_params)
            
            if existing:
                return "updated"
            
            await self._insert_record(connector, quoted_table_name, transformed_row, connector_type, column_types)
            return "inserted"

        # Has non-key columns - try UPDATE first
        set_clause = ", ".join([
            f"{self._quote_identifier(connector_type, col)} = %s" for col in non_key_columns
        ])
        where_clause = self._build_where_clause_with_trim(key_columns, connector_type, column_types)
        params = [transformed_row[col] for col in non_key_columns] + [transformed_row[col] for col in key_columns]
        update_query = f"UPDATE {quoted_table_name} SET {set_clause} WHERE {where_clause}"
        
        updated_rows = await connector.execute_non_query(update_query, tuple(params))
        
        if (updated_rows or 0) > 0:
            return "updated"

        await self._insert_record(connector, quoted_table_name, transformed_row, connector_type, column_types)
        return "inserted"
    
    async def _upsert_with_on_duplicate_key_update(
        self,
        connector,
        quoted_table_name: str,
        row: Dict[str, Any],
        key_columns: List[str],
        non_key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        """
        Use MySQL's ON DUPLICATE KEY UPDATE for atomic upsert operation.
        This is more reliable than UPDATE-then-INSERT because it handles the unique constraint directly.
        """
        all_columns = list(row.keys())
        column_list = ", ".join(self._quote_identifier(connector_type, col) for col in all_columns)
        placeholders = ", ".join(["%s"] * len(all_columns))
        params = tuple(row[col] for col in all_columns)
        
        # Build ON DUPLICATE KEY UPDATE clause - update only non-key columns
        if non_key_columns:
            update_clause = ", ".join([
                f"{self._quote_identifier(connector_type, col)} = VALUES({self._quote_identifier(connector_type, col)})"
                for col in non_key_columns
            ])
            # If there are no non-key columns to update, we still need to handle the duplicate
            # In this case, we'll just update a non-key column to itself (no-op) or skip update
            if not update_clause:
                # All columns are key columns - just return 'updated' without doing anything
                return "updated"
        else:
            # No non-key columns - use a no-op update (update a key column to itself)
            # This will still trigger the "updated" path
            first_key_col = key_columns[0] if key_columns else all_columns[0]
            update_clause = f"{self._quote_identifier(connector_type, first_key_col)} = VALUES({self._quote_identifier(connector_type, first_key_col)})"
        
        query = f"INSERT INTO {quoted_table_name} ({column_list}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {update_clause}"
        
        try:
            affected_rows = await connector.execute_non_query(query, params)
            
            # MySQL returns:
            # - 1 if a new row was inserted
            # - 2 if an existing row was updated
            # - 0 if the row was updated but the values didn't change
            if affected_rows == 1:
                return "inserted"
            elif affected_rows >= 2:
                return "updated"
            else:
                # affected_rows == 0 means the row existed but no changes were made
                return "updated"
        except Exception as e:
            # If ON DUPLICATE KEY UPDATE fails, it might be because:
            # 1. No unique/primary key exists (shouldn't happen if key_columns are set)
            # 2. Some other constraint violation
            # Fallback to regular INSERT (will fail with duplicate error if record exists)
            await self._insert_record(connector, quoted_table_name, row, connector_type, column_types)
            return "inserted"

    def _determine_operation(self, requested_operation: str, key_columns: List[str]) -> str:
        op = (requested_operation or "append").lower()
        if key_columns:
            if op in ("append", "auto", "", "insert"):
                return "upsert"
            if op == "upsert":
                return "upsert"
        if op not in ("append", "overwrite", "upsert"):
            return "append"
        return op

    async def _query_existing_records(
        self,
        connector,
        quoted_table_name: str,
        records: List[Dict[str, Any]],
        key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
        primary_key_column: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Query existing records from database using duplicate check fields.
        Similar to Salesforce bulk query - finds all records that match the key columns.
        
        Args:
            connector: Database connector instance
            quoted_table_name: Quoted table name
            records: List of records to check (we'll extract unique key combinations)
            key_columns: List of column names used for duplicate checking
            connector_type: Database connector type
            column_types: Optional dict mapping column_name -> {data_type, is_nullable}
            primary_key_column: Optional primary key column name (for explicit SELECT)
        
        Returns:
            List of existing records with their primary key/ID
        """
        if not key_columns or not records:
            return []
        
        # Step 1: Extract unique key combinations from records
        unique_keys = set()
        key_to_record = {}  # Map key -> first record with that key (for getting all columns)
        
        for record in records:
            # Build key from key_columns
            key_parts = []
            for col in key_columns:
                value = record.get(col)
                # Normalize: convert None to empty string, trim strings
                if value is None:
                    value = ""
                elif isinstance(value, str):
                    value = value.strip()
                key_parts.append(str(value))
            
            # Only add if all key columns have values
            if all(key_parts):
                key = "|".join(key_parts)
                unique_keys.add(key)
                if key not in key_to_record:
                    key_to_record[key] = record
        
        if not unique_keys:
            return []
        
        # Step 2: Build WHERE clause with OR conditions for all unique keys
        # Format: (col1 = val1 AND col2 = val2) OR (col1 = val3 AND col2 = val4) OR ...
        # Batch queries to avoid SQL query length limits (~65KB for MySQL, ~1GB for PostgreSQL but we'll be conservative)
        # Similar to Salesforce: batch by query length (~15,000 characters)
        MAX_QUERY_LENGTH = 15000  # Conservative limit
        all_existing_records = []
        
        where_conditions = []
        all_params = []
        current_length = 0
        
        for key in unique_keys:
            record = key_to_record[key]
            conditions = []
            record_params = []  # Parameters for this specific record
            
            for col in key_columns:
                quoted_col = self._quote_identifier(connector_type, col)
                value = record.get(col)
                
                # Check if column is a string type - use TRIM() for string columns
                is_string_type = False
                if column_types and col in column_types:
                    data_type = column_types[col].get('data_type', '').lower()
                    is_string_type = any(t in data_type for t in ['char', 'varchar', 'text', 'string'])
                
                # Use TRIM() for string columns to handle whitespace differences
                if is_string_type and connector_type.lower() in ['postgresql', 'postgres']:
                    condition = f"TRIM({quoted_col}) = TRIM(%s)"
                else:
                    condition = f"{quoted_col} = %s"
                
                conditions.append(condition)
                record_params.append(value)
            
            # Combine conditions with AND
            combined_condition = "(" + " AND ".join(conditions) + ")"
            estimated_length = len(combined_condition) + 4  # +4 for " OR "
            
            # Check if adding this would exceed limit
            if current_length + estimated_length > MAX_QUERY_LENGTH and where_conditions:
                # Execute current batch query
                where_clause = " OR ".join(where_conditions)
                
                # Build SELECT clause - explicitly include primary key if known
                if primary_key_column:
                    quoted_pk = self._quote_identifier(connector_type, primary_key_column)
                    query = f"SELECT {quoted_pk}, * FROM {quoted_table_name} WHERE {where_clause}"
                else:
                    query = f"SELECT * FROM {quoted_table_name} WHERE {where_clause}"
                
                batch_records = await connector.execute_query(query, tuple(all_params))
                all_existing_records.extend(batch_records)
                
                
                # Start new batch
                where_conditions = [combined_condition]
                all_params = list(record_params)
                current_length = estimated_length
            else:
                # Add to current batch
                where_conditions.append(combined_condition)
                all_params.extend(record_params)
                current_length += estimated_length
        
        # Step 3: Execute remaining batch query
        if where_conditions:
            where_clause = " OR ".join(where_conditions)
            
            # Build SELECT clause - explicitly include primary key if known
            if primary_key_column:
                quoted_pk = self._quote_identifier(connector_type, primary_key_column)
                query = f"SELECT {quoted_pk}, * FROM {quoted_table_name} WHERE {where_clause}"
            else:
                query = f"SELECT * FROM {quoted_table_name} WHERE {where_clause}"
            
            batch_records = await connector.execute_query(query, tuple(all_params))
            all_existing_records.extend(batch_records)
            
        
        return all_existing_records
    
    def _build_id_mapping_from_query_results(
        self,
        existing_records: List[Dict[str, Any]],
        key_columns: List[str],
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
        primary_key_column: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Build mapping from key combination to record ID (primary key).
        Similar to Salesforce build_id_mapping_from_query_results.
        
        Returns two mappings:
        1. duplicate_check_id_mapping: key -> ID (using key_columns)
        2. full_field_id_mapping: key -> ID (using all fields, for fallback)
        
        Args:
            existing_records: List of existing records from query
            key_columns: List of column names used for duplicate checking
            column_types: Optional dict for type handling
            primary_key_column: Optional primary key column name
        
        Returns:
            Tuple of (duplicate_check_id_mapping, full_field_id_mapping)
        """
        duplicate_check_id_mapping = {}  # key -> record (with ID)
        full_field_id_mapping = {}  # full_key -> record (with ID)
        
        for existing_record in existing_records:
            # Extract ID using primary key column name (if provided)
            record_id = None
            if primary_key_column and primary_key_column in existing_record:
                record_id = existing_record[primary_key_column]
            else:
                # Fallback: try common primary key column names (for backward compatibility)
                for id_col in ['id', 'Id', 'ID', '_id', 'pk', 'primary_key']:
                    if id_col in existing_record:
                        record_id = existing_record[id_col]
                        break
            
            # If no ID found, use first column as fallback (not ideal, but works)
            if not record_id and existing_record:
                record_id = list(existing_record.values())[0]
            
            if not record_id:
                continue
            
            # Build key from key_columns
            key_parts = []
            for col in key_columns:
                value = existing_record.get(col)
                if value is None:
                    value = ""
                elif isinstance(value, str):
                    value = value.strip()
                key_parts.append(str(value))
            
            if all(key_parts):
                key = "|".join(key_parts)
                duplicate_check_id_mapping[key] = record_id
            
            # Build full key from all fields (for fallback matching)
            full_key_parts = []
            for col, value in existing_record.items():
                # Skip primary key column (use dynamic name if provided, otherwise fallback list)
                skip_col = False
                if primary_key_column:
                    skip_col = (col.lower() == primary_key_column.lower())
                else:
                    skip_col = (col.lower() in ['id', '_id', 'pk', 'primary_key'])
                
                if skip_col:
                    continue
                if value is None:
                    value = ""
                elif isinstance(value, str):
                    value = value.strip()
                full_key_parts.append(f"{col}:{value}")
            
            if full_key_parts:
                full_key = "|".join(sorted(full_key_parts))  # Sort for consistency
                full_field_id_mapping[full_key] = record_id
        
        return duplicate_check_id_mapping, full_field_id_mapping
    
    def _split_records_for_bulk_operations_with_indices(
        self,
        records_with_indices: List[Tuple[int, Dict[str, Any]]],
        key_columns: List[str],
        duplicate_check_id_mapping: Dict[str, Any],
        full_field_id_mapping: Dict[str, Any],
        primary_key_column: Optional[str] = None,
    ) -> Tuple[List[Tuple[int, Dict[str, Any]]], List[Tuple[int, Dict[str, Any]]]]:
        """
        Split records into update (has ID) vs insert (no ID) operations, preserving original indices.
        This version tracks the original index along with each record to avoid matching issues.
        
        Args:
            records_with_indices: List of (original_index, record) tuples
            key_columns: List of column names used for duplicate checking
            duplicate_check_id_mapping: Mapping from key -> ID (primary matching)
            full_field_id_mapping: Mapping from full_key -> ID (fallback matching)
        
        Returns:
            Tuple of (records_to_update_with_idx, records_to_insert_with_idx)
            Each is a list of (original_index, record) tuples
        """
        records_to_update_with_idx = []
        records_to_insert_with_idx = []
        
        for orig_list_idx, record in records_with_indices:
            matched = False
            record_id = None
            
            # Try primary matching using key_columns
            key_parts = []
            for col in key_columns:
                value = record.get(col)
                if value is None:
                    value = ""
                elif isinstance(value, str):
                    value = value.strip()
                key_parts.append(str(value))
            
            if all(key_parts):
                key = "|".join(key_parts)
                if key in duplicate_check_id_mapping:
                    record_id = duplicate_check_id_mapping[key]
                    matched = True
            
            # If primary matching failed, try fallback matching using ALL fields
            if not matched:
                full_key_parts = []
                for col, value in record.items():
                    # Skip ID columns
                    if col.lower() in ['id', '_id', 'pk', 'primary_key']:
                        continue
                    if value is None:
                        value = ""
                    elif isinstance(value, str):
                        value = value.strip()
                    full_key_parts.append(f"{col}:{value}")
                
                if full_key_parts:
                    full_key = "|".join(sorted(full_key_parts))
                    if full_key in full_field_id_mapping:
                        record_id = full_field_id_mapping[full_key]
                        matched = True
            
            if matched and record_id:
                # Existing record - add ID for update
                # Create a copy to avoid modifying the original
                record_copy = record.copy()
                pk_col = primary_key_column or 'id'  # Use dynamic primary key, fallback to 'id'
                record_copy[pk_col] = record_id
                records_to_update_with_idx.append((orig_list_idx, record_copy))
            else:
                # New record - no ID for insert
                records_to_insert_with_idx.append((orig_list_idx, record))
        
        return records_to_update_with_idx, records_to_insert_with_idx
    
    def _get_primary_key_column(self, column_types: Dict[str, Dict[str, Any]]) -> Optional[Tuple[str, str]]:
        """
        Extract primary key column name and data type from schema.
        
        Args:
            column_types: Dictionary mapping column_name -> {data_type, is_nullable, is_primary_key}
        
        Returns:
            Tuple of (primary_key_column_name, data_type) or None if not found
        """
        for col_name, col_info in column_types.items():
            if col_info.get('is_primary_key', False):
                return (col_name, col_info.get('data_type', ''))
        return None
    
    def _get_sql_cast_for_column(self, column_name: str, column_types: Optional[Dict[str, Dict[str, Any]]], connector_type: str) -> Optional[str]:
        """
        Determine if a column needs explicit casting in SQL VALUES clause based on schema.
        
        Args:
            column_name: Name of the column
            column_types: Dictionary mapping column_name -> {data_type, is_nullable}
            connector_type: Database connector type (postgresql, mysql, etc.)
        
        Returns:
            SQL cast string (e.g., "CAST(%s AS UUID)") or None if no cast needed
        """
        if not column_types or column_name not in column_types:
            return None
        
        data_type = column_types[column_name].get('data_type', '').lower()
        
        # PostgreSQL specific casts
        if connector_type.lower() in ['postgresql', 'postgres']:
            # UUID type
            if 'uuid' in data_type:
                return "CAST(%s AS UUID)"
            # Timestamp types
            elif 'timestamp' in data_type or 'timestamptz' in data_type:
                return "CAST(%s AS TIMESTAMP)"
            # Date type (but not timestamp)
            elif 'date' in data_type and 'time' not in data_type:
                return "CAST(%s AS DATE)"
            # Time type
            elif 'time' in data_type and 'date' not in data_type and 'timestamp' not in data_type:
                return "CAST(%s AS TIME)"
            # JSON/JSONB types
            elif 'json' in data_type:
                return "CAST(%s AS JSONB)" if 'jsonb' in data_type else "CAST(%s AS JSON)"
            # Array types
            elif '[]' in data_type or 'array' in data_type:
                # Extract base type for array
                base_type = data_type.replace('[]', '').replace('array', '').strip()
                if base_type:
                    return f"CAST(%s AS {base_type.upper()}[])"
            
            # Numeric types usually don't need casting, but can add if needed
            # INTEGER, BIGINT, SMALLINT, etc. are handled automatically
            return None
        
        # MySQL/MariaDB specific casts (usually not needed, but can add if required)
        elif connector_type.lower() in ['mysql', 'mariadb']:
            # MySQL usually handles types automatically via parameter binding
            return None
        
        # MSSQL specific casts
        elif connector_type.lower() in ['mssql', 'sqlserver']:
            if 'uniqueidentifier' in data_type or 'uuid' in data_type:
                return "CAST(%s AS UNIQUEIDENTIFIER)"
            if 'datetime' in data_type or 'date' in data_type:
                return "CAST(%s AS DATETIME2)"
            if 'time' in data_type and 'date' not in data_type:
                return "CAST(%s AS TIME)"
            if 'bit' in data_type:
                return "CAST(%s AS BIT)"
            return None
        
        return None
    
    def _transform_row_values(self, row: Dict[str, Any], column_types: Optional[Dict[str, Dict[str, Any]]], connector_type: str) -> Dict[str, Any]:
        """
        Transform row values based on destination column data types.
        Similar to Salesforce transformation approach.
        
        Args:
            row: Dictionary of column_name -> value
            column_types: Dictionary mapping column_name -> {data_type, is_nullable}
            connector_type: Database connector type
        
        Returns:
            Transformed row dictionary
        """
        if not column_types:
            return row
        
        transformed_row = {}
        for col_name, value in row.items():
            if col_name in column_types:
                col_meta = column_types[col_name]
                transformed_value = convert_value_for_database(
                    value=value,
                    column_type=col_meta.get('data_type', ''),
                    connector_type=connector_type,
                    is_nullable=col_meta.get('is_nullable', True),
                    column_name=col_name
                )
                transformed_row[col_name] = transformed_value
            else:
                # Column not in schema - use as-is
                transformed_row[col_name] = value
        
        return transformed_row
    
    async def _update_batch(
        self,
        connector,
        quoted_table_name: str,
        rows: List[Dict[str, Any]],
        key_columns: List[str],
        connector_type: str,
        column_types: Optional[Dict[str, Dict[str, Any]]] = None,
        primary_key_column: Optional[str] = None,
        primary_key_data_type: Optional[str] = None,
    ) -> int:
        """
        Update multiple records in batch.
        Uses ID (primary key) to identify records, and updates non-key columns.
        
        For PostgreSQL: Uses UPDATE ... FROM VALUES for efficient batch updates
        For MySQL/MariaDB: Uses CASE statement approach for efficient batch updates
        For Others: Updates records in a loop (still more efficient than individual upserts)
        
        Args:
            connector: Database connector instance
            quoted_table_name: Quoted table name
            rows: List of row dictionaries (all must have same columns and include primary key)
            key_columns: List of column names that form the unique key
            connector_type: Database connector type
            column_types: Optional dict mapping column_name -> {data_type, is_nullable}
            primary_key_column: Optional primary key column name (defaults to 'id')
            primary_key_data_type: Optional primary key data type (for casting)
        
        Returns:
            Number of rows updated
        """
        if not rows:
            return 0
        
        # All rows should have the same columns - use first row's columns
        all_columns = list(rows[0].keys())
        # Use dynamic primary key column name
        pk_col = primary_key_column or 'id'  # Fallback to 'id' if not provided
        non_key_columns = [col for col in all_columns if col not in key_columns and col.lower() != pk_col.lower()]
        
        if not non_key_columns:
            # No non-key columns to update - skip
            return 0
        
        # Use dynamic primary key column name
        id_column = pk_col
        
        # Transform all rows based on column types (before building query)
        transformed_rows = []
        for row in rows:
            transformed_row = self._transform_row_values(row, column_types, connector_type)
            transformed_rows.append(transformed_row)
        
        # For PostgreSQL, use UPDATE ... FROM VALUES for efficient batch updates
        if connector_type.lower() in ['postgresql', 'postgres']:
            try:
                # Get SQL cast for ID column based on schema (use provided data type or fetch from schema)
                id_column_type = primary_key_data_type.lower() if primary_key_data_type else (column_types.get(id_column, {}).get('data_type', '').lower() if column_types else '')
                id_cast = self._get_sql_cast_for_column(id_column, column_types, connector_type)
                
                # Get SQL casts for non-key columns
                column_casts = {}
                for col in non_key_columns:
                    cast = self._get_sql_cast_for_column(col, column_types, connector_type)
                    if cast:
                        column_casts[col] = cast
                
                # Build VALUES clause: (id1, col1_val1, col2_val1), (id2, col1_val2, col2_val2), ...
                value_rows = []
                all_params = []
                valid_rows = []
                
                for idx, row in enumerate(transformed_rows):
                    # Use dynamic primary key column name
                    record_id = row.get(id_column) or row.get('id') or row.get('Id') or row.get('ID')
                    if not record_id:
                        continue
                    
                    valid_rows.append(row)
                    # Build row: (id, col1, col2, ...)
                    row_values = [record_id]
                    row_values.extend([row.get(col) for col in non_key_columns])
                    
                    # Build placeholders with casts if needed
                    placeholders = []
                    
                    # ID column placeholder (with cast if needed)
                    placeholders.append(id_cast if id_cast else '%s')
                    
                    # Non-key column placeholders (with casts if needed)
                    for col in non_key_columns:
                        placeholders.append(column_casts.get(col, '%s'))
                    
                    value_rows.append(f"({', '.join(placeholders)})")
                    all_params.extend(row_values)
                
                if not all_params:
                    return 0
                
                # Build SET clause: col1 = v.col1, col2 = v.col2, ...
                set_clause = ", ".join([
                    f"{self._quote_identifier(connector_type, col)} = v.{self._quote_identifier(connector_type, col)}"
                    for col in non_key_columns
                ])
                
                # Build column list for VALUES table
                value_cols = [id_column] + non_key_columns
                value_col_list = ", ".join(self._quote_identifier(connector_type, col) for col in value_cols)
                
                # Build VALUES clause (casts already applied in value_rows)
                values_clause = ", ".join(value_rows)
                
                # Build final query
                query = f"""
                    UPDATE {quoted_table_name} t
                    SET {set_clause}
                    FROM (VALUES {values_clause}) AS v({value_col_list})
                    WHERE t.{self._quote_identifier(connector_type, id_column)} = v.{self._quote_identifier(connector_type, id_column)}
                """
                
                affected_rows = await connector.execute_non_query(query, tuple(all_params))
                
                return affected_rows or len(valid_rows)  # Return number of rows processed
                
            except Exception as e:
                logger.warning("PostgreSQL batch UPDATE failed, falling back to individual updates: %s", e)
                # Fall through to individual update loop
        
        # For MySQL, use CASE statement approach for efficient batch updates
        if connector_type.lower() in ['mysql', 'mariadb']:
            try:
                # First pass: collect valid rows with IDs
                valid_rows = []
                id_list = []
                
                for row in rows:
                    # Use dynamic primary key column name
                    record_id = row.get(id_column) or row.get('id') or row.get('Id') or row.get('ID')
                    if record_id:
                        valid_rows.append(row)
                        id_list.append(record_id)
                
                if not valid_rows:
                    return 0
                
                # Build CASE statements for each column
                # Format: col1 = CASE id WHEN ? THEN ? WHEN ? THEN ? ... END
                set_clauses = []
                all_params = []
                quoted_id_col = self._quote_identifier(connector_type, id_column)
                
                for col in non_key_columns:
                    quoted_col = self._quote_identifier(connector_type, col)
                    case_parts = []
                    
                    for row in valid_rows:
                        # Use dynamic primary key column name
                        record_id = row.get(id_column) or row.get('id') or row.get('Id') or row.get('ID')
                        case_parts.append("WHEN %s THEN %s")
                        all_params.append(record_id)
                        all_params.append(row.get(col))
                    
                    if case_parts:
                        case_statement = f"CASE {quoted_id_col} {' '.join(case_parts)} END"
                        set_clauses.append(f"{quoted_col} = {case_statement}")
                
                if not set_clauses:
                    return 0
                
                # Build final UPDATE query
                set_clause = ", ".join(set_clauses)
                id_placeholders = ", ".join(["%s"] * len(id_list))
                all_params.extend(id_list)  # Add IDs for WHERE clause
                
                query = f"UPDATE {quoted_table_name} SET {set_clause} WHERE {quoted_id_col} IN ({id_placeholders})"
                
                affected_rows = await connector.execute_non_query(query, tuple(all_params))
                return affected_rows or len(valid_rows)
                
            except Exception as e:
                logger.warning("MySQL batch UPDATE failed, falling back to individual updates: %s", e)
                import traceback
                traceback.print_exc()
                # Fall through to individual update loop
        
        # For other databases, update records in a loop
        # This is still more efficient than the old upsert approach because:
        # 1. We only update records we know exist (from query)
        # 2. We don't need to try INSERT first
        # 3. We use ID for efficient lookups
        updated_count = 0
        
        for row in rows:
            # Use dynamic primary key column name
            record_id = row.get(id_column) or row.get('id') or row.get('Id') or row.get('ID')
            if not record_id:
                continue  # Skip if no primary key
            
            # Build SET clause for non-key columns
            set_clause = ", ".join([
                f"{self._quote_identifier(connector_type, col)} = %s" for col in non_key_columns
            ])
            
            # Use ID in WHERE clause for efficiency
            where_clause = f"{self._quote_identifier(connector_type, id_column)} = %s"
            
            # Combine SET params and WHERE params
            set_params = tuple(row[col] for col in non_key_columns)
            all_params = set_params + (record_id,)
            
            update_query = f"UPDATE {quoted_table_name} SET {set_clause} WHERE {where_clause}"
            
            try:
                affected_rows = await connector.execute_non_query(update_query, all_params)
                if affected_rows and affected_rows > 0:
                    updated_count += 1
            except Exception as e:
                logger.debug(f"⚠️ Error updating record with ID {record_id}: {e}")
                # Continue with next record
        
        return updated_count

    def _quote_identifier(self, connector_type: str, identifier: str) -> str:
        if not identifier:
            return identifier

        connector_type = (connector_type or "").lower()
        parts = identifier.split(".")

        def quote_part(part: str) -> str:
            if connector_type in {"mysql", "mariadb"}:
                escaped = part.replace("`", "``")
                return f"`{escaped}`"
            elif connector_type in {"postgresql", "postgres"}:
                escaped = part.replace('"', '""')
                return f'"{escaped}"'
            elif connector_type in {"mssql", "sqlserver"}:
                escaped = part.replace("]", "]]")
                return f"[{escaped}]"
            return part

        quoted_parts = [quote_part(part) for part in parts if part]
        return ".".join(quoted_parts) if quoted_parts else identifier

    def _serialize_for_json(self, data: Any) -> Any:
        """
        Recursively convert datetime/date/UUID objects to JSON-serializable formats.
        
        Args:
            data: Data structure that may contain datetime/date/UUID objects
            
        Returns:
            Data structure with datetime/date/UUID objects converted to strings
        """
        # Handle UUID objects (from PostgreSQL primary keys, etc.)
        if isinstance(data, UUID):
            return str(data)  # Convert UUID to string
        
        # Also handle UUID-like objects (check for hex attribute as fallback)
        if hasattr(data, 'hex') and hasattr(data, 'int'):  # UUID objects have both 'hex' and 'int' attributes
            try:
                return str(data)  # Convert UUID to string
            except:
                return data
        
        if isinstance(data, datetime):
            return data.isoformat()
        elif isinstance(data, date):
            return data.isoformat()
        elif isinstance(data, dict):
            return {key: self._serialize_for_json(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [self._serialize_for_json(item) for item in data]
        else:
            return data

    async def _track_migration_records(
        self,
        batch_id: str,
        user_id: str,
        table_name: str,
        connector_id: str,
        record_results: List[Dict[str, Any]],
        destination_name: Optional[str] = None,
    ):
        try:
            for idx, result in enumerate(record_results):
                record_index = result["record_index"]
                mapped_row = result.get("mapped_row", {})
                source_record = result.get("source_record", {})
                status = result.get("status", "created")
                message = result.get("message") or f"Successfully {status} in Database"

                # Convert datetime/date/UUID objects to JSON-serializable formats
                serialized_mapped_row = self._serialize_for_json(mapped_row)
                serialized_source_record = self._serialize_for_json(source_record)

                record_id = await MigrationRecordsService.create_migration_record(
                    batch_id=batch_id,
                    user_id=user_id,
                    record_index=record_index,
                    source_data=serialized_source_record,
                    mapped_data=serialized_mapped_row,
                )

                dest_by = result.get("destination_by_table")
                if isinstance(dest_by, dict) and dest_by:
                    destination_payload = {}
                    for tbl_name, info in dest_by.items():
                        if not isinstance(info, dict):
                            continue
                        destination_payload[tbl_name] = {
                            "status": info.get("status", status),
                            "message": info.get("message") or message,
                            "connector_id": connector_id,
                            "record_index": record_index + 1,
                            "destination_name": destination_name or tbl_name,
                        }
                else:
                    destination_payload = {
                        table_name: {
                            "status": status,
                            "message": message,
                            "connector_id": connector_id,
                            "record_index": record_index + 1,
                            "destination_name": destination_name or table_name,
                        }
                    }

                if status in ("created", "updated"):
                    record_status = "success"
                    error_message = None
                else:
                    record_status = "failed"
                    error_message = message

                await MigrationRecordsService.update_migration_record_result(
                    record_id=record_id,
                    status=record_status,
                    salesforce_results=destination_payload,
                    error_message=error_message,
                )
        except Exception as e:
            logger.debug(f"Error tracking SQL migration records: {e}")

    async def find_and_delete_created_records(
        self,
        connector_id: str,
        table_name: str,
        records_to_delete: List[Dict[str, Any]],
        key_columns: List[str],
        user_id: str
    ) -> Dict[str, Any]:
        """
        Find created records in database and delete them using their IDs.
        
        Process:
        1. Query database to find records matching the mapped_data
        2. Use key_columns to build WHERE clause for matching
        3. Get record IDs (auto-increment ID or composite primary key)
        4. Delete records using their IDs
        
        Args:
            connector_id: SQL connector ID
            table_name: Table name
            records_to_delete: List of mapped_data dictionaries to delete
            key_columns: Key columns used for matching (same as upsert)
            user_id: User ID for connector access
            
        Returns:
            Dict with deleted_count, failed_count, and errors
        """
        deleted_count = 0
        failed_count = 0
        errors = []
        connector_instance = None
        
        try:
            # Get connector
            service = SQLConnectorService()
            connector_bundle = await service.get_connector_by_id(connector_id, user_id)
            if not connector_bundle:
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": ["Database connector not found or inactive"]
                }
            
            connector_record = dict(connector_bundle["connector"])
            connector_config = service.prepare_connector_config(connector_record)
            connector_type = str(connector_record.get("connector_type", "")).lower()
            
            connector_instance = service.create_connector_instance(
                connector_record["connector_type"],
                connector_config,
            )
            
            quoted_table_name = self._quote_identifier(connector_type, table_name)
            
            # Determine matching columns - use key_columns if provided, otherwise use all fields from mapped_data
            if key_columns:
                matching_columns = key_columns
            elif records_to_delete:
                # Use all fields from mapped_data
                matching_columns = list(records_to_delete[0].keys())
            else:
                return {
                    "deleted_count": 0,
                    "failed_count": 0,
                    "errors": ["No records to delete"]
                }
            
            if not matching_columns:
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": ["No matching columns found"]
                }
            
            # Build bulk DELETE query with OR conditions
            # Format: DELETE FROM table WHERE (col1 = ? AND col2 = ?) OR (col1 = ? AND col2 = ?) ...
            or_conditions = []
            all_params = []
            valid_records = []
            
            for idx, record in enumerate(records_to_delete):
                try:
                    # Build AND conditions for this record
                    and_conditions = []
                    record_params = []
                    
                    for col in matching_columns:
                        if col in record and record[col] is not None:
                            quoted_col = self._quote_identifier(connector_type, col)
                            and_conditions.append(f"{quoted_col} = %s")
                            record_params.append(record[col])
                        else:
                            break
                    else:
                        # All columns found, add this record's condition
                        if and_conditions:
                            or_conditions.append(f"({' AND '.join(and_conditions)})")
                            all_params.extend(record_params)
                            valid_records.append(record)
                except Exception as e:
                    error_msg = f"Error processing record {idx+1}: {str(e)}"
                    errors.append(error_msg)
                    failed_count += 1
                    logger.warning(f"❌ [DELETE-SQL] {error_msg}")
                    import traceback
                    traceback.print_exc()
            
            if not or_conditions:
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": errors if errors else ["No valid records found for deletion"]
                }
            
            # Build and execute bulk DELETE query
            try:
                where_clause = " OR ".join(or_conditions)
                delete_query = f"DELETE FROM {quoted_table_name} WHERE {where_clause}"
                
                # Execute bulk delete
                await connector_instance.execute_non_query(delete_query, tuple(all_params))
                deleted_count = len(valid_records)
                
            except Exception as e:
                error_msg = f"Error executing bulk delete: {str(e)}"
                errors.append(error_msg)
                failed_count = len(valid_records)
                logger.warning(f"❌ [DELETE-SQL] {error_msg}")
                import traceback
                traceback.print_exc()
            
            return {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "errors": errors
            }
            
        except Exception as e:
            error_msg = f"Error in find_and_delete_created_records: {str(e)}"
            logger.warning(f"❌ {error_msg}")
            import traceback
            traceback.print_exc()
            return {
                "deleted_count": 0,
                "failed_count": len(records_to_delete),
                "errors": [error_msg]
            }
        finally:
            # Always close the connection to prevent resource leaks
            if connector_instance:
                try:
                    await connector_instance.close_connection()
                except Exception as close_error:
                    # Don't fail if closing fails - connection might already be closed
                    logger.debug(f"⚠️ Warning: Error closing connection in find_and_delete_created_records: {close_error}")

