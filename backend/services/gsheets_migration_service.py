"""
Google Sheets Migration Service

This service handles migrating data TO Google Sheets as a destination.
Similar to Salesforce migration but writes to Google Sheets instead.
"""

import json
import uuid
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from db.connection import database
from services.google_sheets_service import GoogleSheetsService, GoogleSheetsRateLimitError, GoogleSheetsAPIError
from services.entitlements import get_entitlements
from services.migration_records_service import MigrationRecordsService
from utils.data_type_converter import convert_value_for_sheets
from utils.google_sheets_retry import retry_with_backoff
from utils.batch_config import GSHEETS_BATCH_WRITE_SIZE


class GoogleSheetsMigrationService:
    """Service for migrating data to Google Sheets destination"""
    
    async def migrate_data_to_google_sheets(
        self,
        data: List[Dict],
        mapping: Dict,
        spreadsheet_id: str,
        sheet_name: str,
        user_id: str,
        operation: str = "append",  # "append", "overwrite", "upsert"
        batch_id: Optional[str] = None,
        document_type: Optional[str] = None,
        spreadsheet_name: Optional[str] = None,
        start_index: int = 0,
        migration_job_id: Optional[str] = None,
        job_store=None,
        google_sheets_connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Migrate data to Google Sheets
        
        Args:
            data: List of records to migrate
            mapping: Field mapping (extracted_field -> google_sheets_column)
            spreadsheet_id: Target spreadsheet ID
            sheet_name: Target sheet name
            user_id: User ID
            operation: How to write data (append, overwrite, upsert)
            batch_id: Optional batch ID for tracking
            document_type: Optional document type for tracking
            
        Returns:
            Dict with migration results
        """
        
        try:
            print(f"📊 Starting Google Sheets migration")
            print(f"   Spreadsheet ID: {spreadsheet_id}")
            print(f"   Sheet: {sheet_name}")
            print(f"   Records: {len(data)}")
            print(f"   Operation: {operation}")
            
            # Get Google Sheets connection
            connection = await self._get_user_connection(
                user_id,
                connection_id=google_sheets_connection_id,
                spreadsheet_id=spreadsheet_id,
            )
            if not connection:
                raise Exception("Google Sheets connection not found")
            
            # Create Google Sheets service
            sheets_service = GoogleSheetsService(
                access_token=connection['access_token'],
                refresh_token=connection['refresh_token']
            )
            
            # Prepare mapping metadata and headers
            mapping_entries, ordered_columns, key_columns = self._prepare_mapping_entries(mapping)
            print(f"🛠️ Google Sheets migration mapping entries: {len(mapping_entries)} columns: {ordered_columns}")
            if not mapping_entries:
                print(f"❌ Mapping payload received: {mapping}")
                raise ValueError("No valid field mappings provided")

            effective_op = (operation or "append").lower()
            if key_columns:
                if effective_op in ["append", "auto", ""]:
                    effective_op = "upsert"
            else:
                if effective_op == "upsert":
                    print("⚠️ No duplicate check fields provided; falling back to append.")
                    effective_op = "append"
            
            headers = await self._ensure_headers(
                sheets_service=sheets_service,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                required_columns=ordered_columns
            )
            
            header_index = {header: idx for idx, header in enumerate(headers)}
            
            rows_to_write = [
                self._build_row_values(
                    record=record,
                    mapping_entries=mapping_entries,
                    headers=headers,
                    header_index=header_index
                )
                for record in data
            ]
            
            print(f"   Prepared {len(rows_to_write)} rows with {len(headers)} headers")
            
            # Determine display name for tracking
            spreadsheet_label = spreadsheet_name or sheet_name or spreadsheet_id
            
            # Get or create batch ID
            if not batch_id:
                mapping_config = {
                    "spreadsheet_id": spreadsheet_id,
                    "sheet_name": sheet_name,
                    "spreadsheet_name": spreadsheet_label,
                    "operation": effective_op,
                    "headers": headers,
                    "mapping_entries": mapping_entries,
                    "key_columns": key_columns,
                }
                batch_id = await MigrationRecordsService.create_migration_batch(
                user_id=user_id,
                    batch_name=f"Google_Sheets_{sheet_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                document_type=document_type or "Google Sheets Migration",
                total_records=len(data),
                    mapping_config=mapping_config,
                    destination_type="google_sheets"
            )
            
            # Write data based on operation
            insert_count = 0
            update_count = 0
            if effective_op == "append":
                result = await self._append_data(
                    sheets_service, spreadsheet_id, sheet_name, 
                    rows_to_write, headers
                )
                insert_count = result.get("rows_written", len(rows_to_write))
                update_count = 0
            elif effective_op == "overwrite":
                result = await self._overwrite_data(
                    sheets_service, spreadsheet_id, sheet_name, 
                    rows_to_write, headers
                )
                insert_count = result.get("rows_written", len(rows_to_write))
                update_count = 0
            elif effective_op == "upsert":
                cancel_check = None
                if migration_job_id and job_store:
                    from utils.migration_cancel import make_cancel_check
                    cancel_check = make_cancel_check(migration_job_id, job_store)
                result = await self._upsert_data(
                    sheets_service, spreadsheet_id, sheet_name,
                    data=data,
                    mapping_entries=mapping_entries,
                    headers=headers,
                    header_index=header_index,
                    key_columns=key_columns,
                    cancel_check=cancel_check,
                )
                insert_count = result.get("rows_inserted", result.get("rows_written", 0))
                update_count = result.get("rows_updated", 0)
            else:
                raise ValueError(f"Unsupported operation: {effective_op}")
            
            processed_records = insert_count + update_count if effective_op == "upsert" else insert_count
            
            # Update batch status
            await MigrationRecordsService.update_batch_status(
                batch_id=batch_id,
                status="completed",
                successful_count=processed_records,
                failed_count=0
            )
            
            # Track individual records
            await self._track_migration_records(
                batch_id=batch_id,
                user_id=user_id,
                data=data,
                mapping_entries=mapping_entries,
                headers=headers,
                insert_count=insert_count,
                update_count=update_count,
                spreadsheet_id=spreadsheet_id,
                sheet_name=sheet_name,
                spreadsheet_name=spreadsheet_label,
                start_index=start_index
            )
            
            records_processed = processed_records
            if records_processed:
                await get_entitlements().track_successful_migration(user_id, records_processed)

            # Deduct credits: 1 per record (same as record migration)
            if records_processed:
                try:
                    from utils.credit_config import compute_migration_credits
                    user_plan = await get_entitlements().get_user_plan(user_id)
                    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
                    if credits_per_month is not None and credits_per_month != -1:
                        migration_credits = compute_migration_credits(records_processed)
                        if migration_credits > 0:
                            await get_entitlements().deduct(
                                user_id=user_id,
                                amount=migration_credits,
                                reference_type="migration",
                                reference_id=batch_id or "gsheets",
                            )
                            print(f"✅ [GSHEETS_MIGRATION] Deducted {migration_credits} credits ({records_processed} records)")
                except Exception as e:
                    print(f"⚠️ [GSHEETS_MIGRATION] Failed to deduct credits: {e}")
            
            print(f"✅ Google Sheets migration completed successfully")
            
            return {
                "status": "success",
                "batch_id": batch_id,
                "records_migrated": records_processed,
                "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}",
                "sheet_name": sheet_name,
                "operation": effective_op,
                "result": result,
                "key_columns": key_columns,
                "processed_records": records_processed,
                "rows_inserted": insert_count,
                "rows_updated": update_count,
                "start_index": start_index,
                "formatted_results": self._format_results_for_frontend(
                    data=data,
                    spreadsheet_name=spreadsheet_label,
                    sheet_name=sheet_name,
                    insert_count=insert_count,
                    update_count=update_count,
                    start_index=start_index
                )
            }
            
        except GoogleSheetsRateLimitError as e:
            # Handle rate limit errors with user-friendly message
            user_message = (
                "Google Sheets rate limit reached. "
                "The migration will be retried automatically. "
                "If this persists, please wait a few minutes and try again."
            )
            
            print(f"❌ Google Sheets rate limit error: {e}")
            if e.retry_after:
                print(f"   Retry after: {e.retry_after} seconds")
            
            # Update batch status with user-friendly message
            if batch_id:
                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="failed",
                    successful_count=0,
                    failed_count=len(data),
                    error_summary=user_message
                )
            
            raise Exception(user_message)
            
        except GoogleSheetsAPIError as e:
            # Handle other API errors with user-friendly message
            if e.status_code == 403:
                user_message = (
                    "Permission denied. I don't have write access to this spreadsheet. "
                    "Please ensure the spreadsheet is shared with your Google account and try again."
                )
            elif e.status_code == 404:
                user_message = (
                    "Spreadsheet not found. Please check the spreadsheet ID or URL and try again."
                )
            else:
                user_message = (
                    f"Google Sheets API error (Status: {e.status_code}). "
                    "Please try again later or contact support if the issue persists."
                )
            
            print(f"❌ Google Sheets API error: {e}")
            print(f"   Status code: {e.status_code}")
            if e.error_details:
                print(f"   Details: {e.error_details}")
            
            if batch_id:
                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="failed",
                    successful_count=0,
                    failed_count=len(data),
                    error_summary=user_message
                )
            
            raise Exception(user_message)
            
        except Exception as e:
            # Generic error handling
            error_message = str(e)
            
            # Check if it's a rate limit error in the message (fallback detection)
            if "429" in error_message or "rate limit" in error_message.lower() or "quota exceeded" in error_message.lower():
                user_message = (
                    "Google Sheets rate limit reached. "
                    "Please wait a few minutes and try again."
                )
            elif "403" in error_message or "permission" in error_message.lower():
                user_message = (
                    "Permission denied. I don't have write access to this spreadsheet. "
                    "Please ensure the spreadsheet is shared with your Google account and try again."
                )
            elif "404" in error_message or "not found" in error_message.lower():
                user_message = (
                    "Spreadsheet not found. Please check the spreadsheet ID or URL and try again."
                )
            else:
                user_message = f"Migration to Google Sheets failed: {error_message}"
            
            print(f"❌ Google Sheets migration failed: {error_message}")
            
            if batch_id:
                await MigrationRecordsService.update_batch_status(
                    batch_id=batch_id,
                    status="failed",
                    successful_count=0,
                    failed_count=len(data),
                    error_summary=user_message
                )
            
            raise Exception(user_message)
    
    def _prepare_mapping_entries(
        self, 
        mapping: Dict
    ) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
        """
        Normalize mapping dict into ordered entries and extract key columns.
        Handles both simple mappings (dict) and array mappings (list) for split fields.
        """
        entries: List[Dict[str, Any]] = []
        for field, info in mapping.items():
            # Handle array mappings (split fields)
            if isinstance(info, list):
                # Array mapping - process each item in the array
                for idx, item in enumerate(info):
                    if not isinstance(item, dict):
                        print(f"⚠️ Skipping array item {idx} for {field}: expected dict, got {type(item)}")
                        continue
                    column = item.get("column")
                    if not column:
                        print(f"⚠️ Skipping array item {idx} for {field}: missing 'column' entry")
                        continue
                    entry = {
                        "field": field,
                        "column": column,
                        "position": item.get("position", len(entries)),
                        "is_duplicate_check": item.get("is_duplicate_check", False),
                        "is_combined_field": item.get("is_combined_field", False),
                    }
                    entries.append(entry)
                    print(f"   ↳ Prepared entry (split field {idx}): {entry}")
            elif isinstance(info, dict):
                # Simple mapping (one-to-one)
                column = info.get("column")
                if not column:
                    print(f"⚠️ Skipping mapping for {field}: missing 'column' entry")
                    continue
                entry = {
                    "field": field,
                    "column": column,
                    "position": info.get("position", len(entries)),
                    "is_duplicate_check": info.get("is_duplicate_check", False),
                    "is_combined_field": info.get("is_combined_field", False),
                }
                entries.append(entry)
                print(f"   ↳ Prepared entry: {entry}")
            else:
                print(f"⚠️ Skipping mapping for {field}: expected dict or list, got {type(info)}")
                continue

        entries = self._dedupe_mapping_entries_by_column(entries)
        entries.sort(key=lambda item: item["position"])
        ordered_columns = [entry["column"] for entry in entries]
        key_columns = [entry["column"] for entry in entries if entry["is_duplicate_check"]]
        
        print(f"✅ Prepared {len(entries)} mapping entries; duplicate keys: {key_columns}")
        return entries, ordered_columns, key_columns

    @staticmethod
    def _dedupe_mapping_entries_by_column(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Keep one mapping entry per destination column (truncates duplicate combine rows."""
        by_column: Dict[str, Dict[str, Any]] = {}
        for entry in entries:
            column = entry.get("column")
            if not column:
                continue
            existing = by_column.get(column)
            if existing is None:
                by_column[column] = entry
                continue
            if entry.get("is_duplicate_check") and not existing.get("is_duplicate_check"):
                by_column[column] = entry
                continue
            if entry.get("is_combined_field") and not existing.get("is_combined_field"):
                by_column[column] = entry
                continue
            if not entry.get("is_combined_field") and existing.get("is_combined_field"):
                continue
        return list(by_column.values())
    
    async def _ensure_headers(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        required_columns: List[str]
    ) -> List[str]:
        """
        Ensure the sheet has a header row and includes all required columns.
        Returns the final list of headers in the sheet.
        """
        header_result = await sheets_service.read_spreadsheet(
            spreadsheet_id=spreadsheet_id,
            range_name='A1:ZZ1',
            sheet_name=sheet_name
        )
        
        headers: List[str] = header_result.get("headers", []) if header_result.get("success") else []
        
        if not headers:
            headers = list(required_columns)
            if headers:
                await sheets_service.write_data(
                    spreadsheet_id,
                    f"'{sheet_name}'!A1",
                    [headers]
                )
            return headers
        
        updated = False
        header_set = set(h for h in headers if h)
        for column in required_columns:
            if column and column not in header_set:
                headers.append(column)
                header_set.add(column)
                updated = True
        
        if updated:
            await sheets_service.write_data(
                spreadsheet_id,
                f"'{sheet_name}'!A1",
                [headers]
            )
        
        return headers
    
    def _build_row_values(
        self,
        record: Dict[str, Any],
        mapping_entries: List[Dict[str, Any]],
        headers: List[str],
        header_index: Dict[str, int],
        base_values: Optional[List[str]] = None
    ) -> List[str]:
        """
        Build a row aligned with sheet headers using provided mapping.
        Optionally start from existing row values to preserve unmapped columns.
        Applies type conversion for better data quality in Google Sheets.
        """
        row_values = list(base_values) if base_values is not None else [''] * len(headers)
        
        for entry in mapping_entries:
            column = entry["column"]
            idx = header_index.get(column)
            if idx is None or idx >= len(row_values):
                continue
            
            value = record.get(entry["field"], "")
            if (value is None or value == "") and entry.get("field"):
                field_key = str(entry["field"])
                field_lower = field_key.lower()
                for rk, rv in record.items():
                    if str(rk).lower() == field_lower:
                        value = rv
                        break
            
            # Skip empty strings when base_values is provided (upsert mode) to preserve existing values
            # For append mode (no base_values), empty strings are acceptable for new rows
            if base_values is not None:
                # In upsert mode, skip None or empty strings to preserve existing cell values
                if value is None or (isinstance(value, str) and value.strip() == ''):
                    continue  # Preserve existing value from base_values
            
            # Apply type conversion for Google Sheets
            try:
                converted_value = convert_value_for_sheets(value, column_name=column)
                # Convert to string for Google Sheets (it handles types internally)
                if converted_value is None:
                    # Only set empty string for append mode (new rows)
                    if base_values is None:
                        row_values[idx] = ""
                    # For upsert mode, None values are already skipped above
                elif isinstance(converted_value, bool):
                    # Google Sheets recognizes boolean values
                    row_values[idx] = str(converted_value).lower() if converted_value else "FALSE"
                elif isinstance(converted_value, (int, float)):
                    # Google Sheets recognizes numbers
                    row_values[idx] = str(converted_value)
                else:
                    row_values[idx] = str(converted_value)
            except Exception as e:
                # If conversion fails, use original value as string
                print(f"⚠️ Type conversion error for column {column}: {e}, using original value")
                # Only set value if it's not empty (for upsert mode, preserve existing)
                if base_values is None or (value is not None and isinstance(value, str) and value.strip() != ''):
                    row_values[idx] = ""
        
        return row_values
    
    async def _append_data(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        rows: List[List[str]],
        headers: List[str]
    ) -> Dict:
        """Append data to the end of the sheet"""
        
        try:
            header_row = headers
            
            if header_row:
                existing_header_result = await sheets_service.read_spreadsheet(
                    spreadsheet_id=spreadsheet_id,
                    range_name='A1:ZZ1',
                    sheet_name=sheet_name
                )
                has_headers = existing_header_result.get("success") and existing_header_result.get("headers")
                if not has_headers:
                    await sheets_service.write_data(
                        spreadsheet_id,
                        f"'{sheet_name}'!A1",
                        [header_row]
                    )
                    print(f"   Added header row to sheet")
            
            # Append data rows
            if rows:
                result = await sheets_service.append_data(
                    spreadsheet_id,
                    f"'{sheet_name}'!A:ZZ",
                    rows
                )
            
            print(f"   Appended {len(rows)} rows")
            
            return {
                "operation": "append",
                "rows_written": len(rows),
                "range": result.get('updates', {}).get('updatedRange', 'Unknown'),
                "processed_records": len(rows)
            }
            
        except Exception as e:
            print(f"❌ Error appending data: {e}")
            raise
    
    async def _overwrite_data(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        rows: List[List[str]],
        headers: List[str]
    ) -> Dict:
        """Overwrite existing data in the sheet"""
        
        try:
            header_row = headers
            
            # Clear existing data
            await sheets_service.clear_data(
                spreadsheet_id,
                f"'{sheet_name}'!A:ZZ"
            )
            print(f"   Cleared existing data")
            
            # Write headers + data
            all_rows = [header_row] + rows if header_row else rows
            result = await sheets_service.write_data(
                spreadsheet_id,
                f"'{sheet_name}'!A1",
                all_rows
            )
            
            print(f"   Wrote {len(rows)} rows (overwrite)")
            
            return {
                "operation": "overwrite",
                "rows_written": len(rows),
                "range": result.get('updatedRange', 'Unknown'),
                "processed_records": len(rows)
            }
            
        except Exception as e:
            print(f"❌ Error overwriting data: {e}")
            raise
    
    async def _upsert_data(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        data: List[Dict[str, Any]],
        mapping_entries: List[Dict[str, Any]],
        headers: List[str],
        header_index: Dict[str, int],
        key_columns: List[str],
        cancel_check=None,
    ) -> Dict:
        """Upsert data (update if exists, insert if not)"""
        from services.google_sheets.upsert_engine import UpsertEngine

        engine = UpsertEngine()
        result = await engine.upsert_records(
            sheets_service=sheets_service,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            records=data,
            headers=headers,
            key_columns=key_columns,
            mapping_entries=mapping_entries,
            cancel_check=cancel_check,
        )
        return {
            "operation": "upsert",
            "rows_written": result.rows_inserted,
            "rows_inserted": result.rows_inserted,
            "rows_updated": result.rows_updated,
            "processed_records": result.processed_records,
        }

    async def find_and_delete_rows(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        records_to_delete: List[Dict[str, Any]],
        key_columns: List[str],
    ) -> Dict[str, Any]:
        """
        Find rows matching the records and delete them in batch.
        
        Process:
        1. Read all data from the sheet
        2. Match records using key_columns (same logic as upsert)
        3. Get row indices of matched rows
        4. Delete rows in batch (descending order to avoid index shifts)
        
        Args:
            sheets_service: GoogleSheetsService instance
            spreadsheet_id: Spreadsheet ID
            sheet_name: Sheet name
            records_to_delete: List of mapped_data dictionaries to delete
            key_columns: Key columns used for matching (same as upsert)
            
        Returns:
            Dict with deleted_count, failed_count, and errors
        """
        deleted_count = 0
        failed_count = 0
        errors = []
        
        def _coerce_record_dict(raw: Any) -> Dict[str, Any]:
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str) and raw.strip():
                try:
                    import json as _json
                    parsed = _json.loads(raw)
                    return parsed if isinstance(parsed, dict) else {}
                except Exception:
                    return {}
            return {}

        def _set_row_value(
            row_values: List[str],
            field: str,
            value: Any,
            header_index: Dict[str, int],
            header_index_lower: Dict[str, int],
        ) -> None:
            idx = header_index.get(field)
            if idx is None:
                idx = header_index_lower.get(str(field).lower())
            if idx is None or idx >= len(row_values):
                return
            row_values[idx] = "" if value is None else str(value).strip()
        
        try:
            read_result = await sheets_service.read_spreadsheet(
                spreadsheet_id=spreadsheet_id,
                range_name='A1:ZZ',
                sheet_name=sheet_name
            )
            
            if not read_result.get("success"):
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": [f"Failed to read sheet: {read_result.get('error', 'Unknown error')}"],
                }
            
            existing_data = read_result.get("data", [])
            existing_headers = read_result.get("headers", [])
            
            if not existing_headers:
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": ["No headers found in sheet"],
                }
            
            header_index = {header: idx for idx, header in enumerate(existing_headers)}
            header_index_lower = {
                str(header).lower(): idx for idx, header in enumerate(existing_headers)
            }

            from services.google_sheets.row_builder import infer_key_columns

            effective_key_columns = list(key_columns or [])
            if not effective_key_columns:
                effective_key_columns = infer_key_columns(existing_headers)
            if not effective_key_columns and records_to_delete:
                sample = _coerce_record_dict(records_to_delete[0])
                if sample:
                    preferred = list(sample.keys())
                    effective_key_columns = infer_key_columns(
                        existing_headers,
                        preferred=preferred,
                    )
            key_indexes = []
            for col in effective_key_columns:
                idx = header_index.get(col)
                if idx is None:
                    idx = header_index_lower.get(str(col).lower())
                if idx is not None:
                    key_indexes.append(idx)

            if not key_indexes:
                return {
                    "deleted_count": 0,
                    "failed_count": len(records_to_delete),
                    "errors": [
                        f"No matching key columns found (tried {effective_key_columns!r}, "
                        f"headers={existing_headers!r})"
                    ],
                }

            def _row_key(row_values: List[str]) -> Tuple[str, ...]:
                return tuple(
                    row_values[key_idx].strip().lower() for key_idx in key_indexes
                )

            def _key_is_matchable(key: Tuple[str, ...]) -> bool:
                return bool(key_indexes) and any(part for part in key)

            key_map: Dict[Tuple[str, ...], int] = {}

            for idx, record in enumerate(existing_data):
                base_row = [''] * len(existing_headers)
                if isinstance(record, dict):
                    for header, value in record.items():
                        _set_row_value(
                            base_row, header, value, header_index, header_index_lower
                        )

                row_index = idx + 2
                key = _row_key(base_row)
                if _key_is_matchable(key):
                    key_map[key] = row_index

            sample_keys = list(key_map.keys())[:5]
            rows_to_delete = []

            for raw_record in records_to_delete:
                record = _coerce_record_dict(raw_record)
                record_row_values = [''] * len(existing_headers)
                for field, value in record.items():
                    _set_row_value(
                        record_row_values, field, value, header_index, header_index_lower
                    )

                record_key = _row_key(record_row_values)
                matchable = _key_is_matchable(record_key)
                in_map = record_key in key_map

                key_vals = {
                    col: record_row_values[key_indexes[i]]
                    for i, col in enumerate(effective_key_columns)
                    if i < len(key_indexes)
                }

                if matchable and in_map:
                    row_index = key_map[record_key]
                    if row_index > 1:
                        rows_to_delete.append(row_index)
                else:
                    failed_count += 1
                    hint = ""
                    if matchable and key_map:
                        hint = f" (sheet has {len(key_map)} keyed rows; sample={sample_keys[:3]!r})"
                    elif not matchable:
                        hint = " (rollback key empty — check mapped_data Email value)"
                    err = (
                        f"Record not found in sheet (key={record_key}, "
                        f"columns={effective_key_columns}, key_vals={key_vals!r}){hint}"
                    )
                    errors.append(err)
            
            # Remove duplicates and sort in descending order (to avoid index shifts)
            rows_to_delete = sorted(set(rows_to_delete), reverse=True)
            
            if not rows_to_delete:
                return {
                    "deleted_count": 0,
                    "failed_count": failed_count,
                    "errors": errors,
                }
            
            # Delete rows using batchUpdate
            try:
                requests = []
                for row_index in rows_to_delete:
                    requests.append({
                        "deleteDimension": {
                            "range": {
                                "sheetId": None,  # Will be resolved by sheet name
                                "dimension": "ROWS",
                                "startIndex": row_index - 1,  # 0-based index
                                "endIndex": row_index  # Delete single row
                            }
                        }
                    })
                
                # Get sheet ID from sheet name (run in thread to avoid blocking event loop)
                spreadsheet = await asyncio.to_thread(
                    lambda: sheets_service.service.spreadsheets().get(
                        spreadsheetId=spreadsheet_id
                    ).execute()
                )
                
                sheet_id = None
                for sheet in spreadsheet.get('sheets', []):
                    if sheet['properties']['title'] == sheet_name:
                        sheet_id = sheet['properties']['sheetId']
                        break
                
                if not sheet_id:
                    return {
                        "deleted_count": 0,
                        "failed_count": len(rows_to_delete),
                        "errors": [f"Sheet '{sheet_name}' not found"]
                    }
                
                # Update requests with sheet ID
                for request in requests:
                    request["deleteDimension"]["range"]["sheetId"] = sheet_id
                
                # Execute batch update (run in thread to avoid blocking event loop)
                body = {
                    "requests": requests
                }
                
                await asyncio.to_thread(
                    lambda: sheets_service.service.spreadsheets().batchUpdate(
                        spreadsheetId=spreadsheet_id,
                        body=body
                    ).execute()
                )
                
                deleted_count = len(rows_to_delete)
                
            except Exception as e:
                error_msg = f"Failed to delete rows: {str(e)}"
                errors.append(error_msg)
                failed_count += len(rows_to_delete)
            
            return {
                "deleted_count": deleted_count,
                "failed_count": failed_count,
                "errors": errors,
            }
            
        except Exception as e:
            error_msg = f"Error in find_and_delete_rows: {str(e)}"
            return {
                "deleted_count": 0,
                "failed_count": len(records_to_delete),
                "errors": [error_msg],
            }
    
    async def _get_user_connection(
        self,
        user_id: str,
        connection_id: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Get Google Sheets OAuth tokens for the correct connected account."""
        try:
            from utils.google_sheets_auth import get_valid_google_sheets_token
            from services.google_sheets_connection_service import connection_id_for_spreadsheet

            cid = connection_id
            if not cid and spreadsheet_id:
                cid = await connection_id_for_spreadsheet(user_id, spreadsheet_id)
            return await get_valid_google_sheets_token(user_id, cid)
        except Exception as e:
            print(f"Error fetching connection: {e}")
            return None
    
    async def _track_migration_records(
        self,
        batch_id: str,
        user_id: str,
        data: List[Dict],
        mapping_entries: List[Dict[str, Any]],
        headers: List[str],
        insert_count: int,
        update_count: int,
        spreadsheet_id: str,
        sheet_name: str,
        spreadsheet_name: str,
        start_index: int = 0
    ):
        """Track individual migration records"""
        try:
            processed_records = insert_count + update_count if (insert_count or update_count) else len(data)
            header_index = {header: idx for idx, header in enumerate(headers)}
            
            for idx, record in enumerate(data[:processed_records]):
                row_values = self._build_row_values(
                    record=record,
                    mapping_entries=mapping_entries,
                    headers=headers,
                    header_index=header_index,
                )
                mapped_row: Dict[str, Any] = {}
                for entry in mapping_entries:
                    column = entry.get("column")
                    if not column:
                        continue
                    col_idx = header_index.get(column)
                    if col_idx is None or col_idx >= len(row_values):
                        continue
                    mapped_row[column] = row_values[col_idx]
                
                record_id = await MigrationRecordsService.create_migration_record(
                    batch_id=batch_id,
                    user_id=user_id,
                    record_index=start_index + idx,
                    source_data=record,
                    mapped_data=mapped_row
                )
                
                status = "created" if idx < insert_count else "updated"
                message = (
                    "Successfully created in Google Sheets"
                    if status == "created"
                    else "Successfully updated in Google Sheets"
                )
                destination_payload = {
                    spreadsheet_name: {
                        "id": spreadsheet_id,
                        "sheet_name": sheet_name,
                        "status": status,
                        "message": message
                    }
                }

                await MigrationRecordsService.update_migration_record_result(
                    record_id=record_id,
                    status="success",
                    salesforce_results=destination_payload
                )
        except Exception as e:
            print(f"Error tracking records: {e}")

    def _format_results_for_frontend(
        self,
        data: List[Dict],
        spreadsheet_name: str,
        sheet_name: str,
        insert_count: int,
        update_count: int,
        start_index: int = 0
    ) -> List[Dict[str, Dict[str, Any]]]:
        """Format results to match Salesforce mapper expectations."""
        processed_records = insert_count + update_count if (insert_count or update_count) else len(data)
        formatted_results: List[Dict[str, Dict[str, Any]]] = []

        for idx, record in enumerate(data[:processed_records]):
            status = "created" if idx < insert_count else "updated"
            message = (
                "Successfully created in Google Sheets"
                if status == "created"
                else "Successfully updated in Google Sheets"
            )
            formatted_results.append({
                spreadsheet_name: {
                    "status": status,
                    "id": None,
                    "message": message,
                    "sheet_name": sheet_name,
                    "record_index": start_index + idx + 1
                }
            })

        return formatted_results

