"""Unified upsert for chat and migration paths."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.google_sheets.config import gsheets_chat_max_rows
from services.google_sheets.models import UpsertResult
from services.google_sheets.row_builder import build_row_values, identity_mapping_entries
from services.google_sheets_service import GoogleSheetsService
from utils.batch_config import GSHEETS_BATCH_WRITE_SIZE
from utils.google_sheets_retry import retry_with_backoff


class UpsertEngine:
    async def upsert_records(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        records: List[Dict[str, Any]],
        headers: List[str],
        key_columns: List[str],
        mapping_entries: Optional[List[Dict[str, Any]]] = None,
        max_existing_rows: Optional[int] = None,
        cancel_check: Optional[Callable] = None,
    ) -> UpsertResult:
        if not records:
            return UpsertResult()

        entries = mapping_entries or identity_mapping_entries(headers)
        header_index = {h: i for i, h in enumerate(headers)}

        if not key_columns:
            rows = [
                build_row_values(r, entries, headers, header_index)
                for r in records
            ]
            inserted = await self._append_rows(
                sheets_service, spreadsheet_id, sheet_name, rows, cancel_check
            )
            return UpsertResult(
                rows_inserted=inserted,
                processed_records=inserted,
            )

        max_rows = max_existing_rows or gsheets_chat_max_rows()
        range_name = f"A1:ZZ{max_rows + 1}"
        read_result = await sheets_service.read_spreadsheet(
            spreadsheet_id=spreadsheet_id,
            range_name=range_name,
            sheet_name=sheet_name,
        )
        existing_data = read_result.get("data", []) if read_result.get("success") else []
        existing_headers = read_result.get("headers", headers) if read_result.get("success") else list(headers)

        for column in headers:
            if column not in existing_headers:
                existing_headers.append(column)

        header_index = {h: i for i, h in enumerate(existing_headers)}
        key_indexes = [header_index[c] for c in key_columns if c in header_index]

        if not key_indexes:
            rows = [
                build_row_values(r, entries, existing_headers, header_index)
                for r in records
            ]
            inserted = await self._append_rows(
                sheets_service, spreadsheet_id, sheet_name, rows, cancel_check
            )
            return UpsertResult(rows_inserted=inserted, processed_records=inserted)

        key_map: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        for idx, record in enumerate(existing_data):
            base_row = [""] * len(existing_headers)
            for header, value in record.items():
                if header in header_index:
                    base_row[header_index[header]] = "" if value is None else str(value)
            row_index = idx + 2
            entry = {"row_index": row_index, "values": base_row}
            key = tuple(base_row[ki].strip().lower() for ki in key_indexes)
            if all(key):
                key_map[key] = entry

        rows_to_append: List[List[str]] = []
        rows_to_update: Dict[int, List[str]] = {}
        update_count = 0
        insert_count = 0

        for record in records:
            new_row_values = build_row_values(
                record, entries, existing_headers, header_index
            )
            key = tuple(new_row_values[ki].strip().lower() for ki in key_indexes)

            if all(key) and key in key_map:
                entry = key_map[key]
                merged = build_row_values(
                    record,
                    entries,
                    existing_headers,
                    header_index,
                    base_values=entry["values"],
                )
                rows_to_update[entry["row_index"]] = merged
                update_count += 1
            else:
                rows_to_append.append(new_row_values)
                insert_count += 1

        batch_size = max(1, GSHEETS_BATCH_WRITE_SIZE)
        update_items = list(rows_to_update.items())

        if update_items:
            for i in range(0, len(update_items), batch_size):
                if cancel_check and await cancel_check():
                    from utils.migration_cancel import MigrationCancelledError

                    raise MigrationCancelledError("Migration cancelled by user")
                batch = update_items[i : i + batch_size]
                batch_updates = [
                    {"range": f"'{sheet_name}'!A{row_index}", "values": [values]}
                    for row_index, values in batch
                ]
                await retry_with_backoff(
                    sheets_service.batch_write_data,
                    spreadsheet_id=spreadsheet_id,
                    updates=batch_updates,
                )
                if i + batch_size < len(update_items):
                    await asyncio.sleep(0.5)

        if rows_to_append:
            if cancel_check and await cancel_check():
                from utils.migration_cancel import MigrationCancelledError

                raise MigrationCancelledError("Migration cancelled by user")
            await retry_with_backoff(
                sheets_service.append_data,
                spreadsheet_id=spreadsheet_id,
                range_name=f"'{sheet_name}'!A:ZZ",
                values=rows_to_append,
            )

        processed = update_count + insert_count
        return UpsertResult(
            rows_inserted=insert_count,
            rows_updated=update_count,
            processed_records=processed,
        )

    async def _append_rows(
        self,
        sheets_service: GoogleSheetsService,
        spreadsheet_id: str,
        sheet_name: str,
        rows: List[List[str]],
        cancel_check: Optional[Callable] = None,
    ) -> int:
        if not rows:
            return 0
        if cancel_check and await cancel_check():
            from utils.migration_cancel import MigrationCancelledError

            raise MigrationCancelledError("Migration cancelled by user")
        await retry_with_backoff(
            sheets_service.append_data,
            spreadsheet_id=spreadsheet_id,
            range_name=f"'{sheet_name}'!A:ZZ",
            values=rows,
        )
        return len(rows)
