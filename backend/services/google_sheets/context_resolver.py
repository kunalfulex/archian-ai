"""Resolve spreadsheet context for executor v2."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from db.connection import database
from services.google_sheets.models import SpreadsheetContext
from services.google_sheets.row_builder import infer_key_columns
from services.google_sheets_service import GoogleSheetsService


async def enrich_context(
    ctx: SpreadsheetContext,
    user_id: str,
    google_sheets_connection: Optional[Dict[str, Any]] = None,
    use_cache: bool = True,
) -> SpreadsheetContext:
    if not ctx.found or not ctx.spreadsheet_id:
        return ctx

    if use_cache:
        from services.google_sheets.context_cache import get_cached_context, set_cached_context

        cached = await get_cached_context(
            str(user_id), ctx.spreadsheet_id, ctx.sheet_name or "Sheet1"
        )
        if cached and cached.headers:
            return cached

    headers: List[str] = []
    key_columns: List[str] = list(ctx.key_columns)

    schema = await database.fetch_one(
        """
        SELECT header_schema, google_sheets_connection_id
        FROM gsheets_extracted_schemas
        WHERE spreadsheet_id = :spreadsheet_id AND user_id = :user_id
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {"spreadsheet_id": ctx.spreadsheet_id, "user_id": str(user_id)},
    )
    if schema:
        schema_row = dict(schema)
        if not ctx.google_sheets_connection_id and schema_row.get("google_sheets_connection_id"):
            ctx.google_sheets_connection_id = str(schema_row["google_sheets_connection_id"])
        header_schema = schema_row.get("header_schema")
        if isinstance(header_schema, str):
            header_schema = json.loads(header_schema)
        if isinstance(header_schema, dict):
            headers = [c["name"] for c in header_schema.get("headers", []) if c.get("name")]

    if not headers and google_sheets_connection:
        sheets_service = GoogleSheetsService(
            access_token=google_sheets_connection["access_token"],
            refresh_token=google_sheets_connection.get("refresh_token"),
            expires_at=google_sheets_connection.get("expires_at"),
        )
        sheet = ctx.sheet_name or "Sheet1"
        result = await sheets_service.read_spreadsheet(
            ctx.spreadsheet_id,
            range_name="A1:ZZ1",
            sheet_name=sheet,
        )
        if result.get("success"):
            headers = result.get("headers") or []

    if headers and not key_columns:
        key_columns = infer_key_columns(headers)

    ctx.headers = headers
    ctx.key_columns = key_columns

    if use_cache and headers:
        from services.google_sheets.context_cache import set_cached_context

        await set_cached_context(str(user_id), ctx)

    return ctx


def _context_from_detection(detection: Dict[str, Any]) -> SpreadsheetContext:
    if not detection:
        return SpreadsheetContext(found=False)
    if detection.get("error") == "ambiguous_spreadsheet_name":
        return SpreadsheetContext(
            found=False,
            error="ambiguous_spreadsheet_name",
            message=detection.get("message"),
            candidates=list(detection.get("candidates") or []),
        )
    if not detection.get("found"):
        return SpreadsheetContext(
            found=False,
            message=detection.get("message") if detection else None,
            error=detection.get("error") if detection else None,
            candidates=list(detection.get("candidates") or []) if detection else [],
        )
    return SpreadsheetContext(
        found=True,
        spreadsheet_id=detection.get("spreadsheet_id"),
        spreadsheet_name=detection.get("spreadsheet_name"),
        sheet_name=detection.get("sheet_name") or "Sheet1",
        source=detection.get("source", ""),
        google_sheets_connection_id=detection.get("google_sheets_connection_id"),
        google_account_email=detection.get("google_account_email"),
    )


async def resolve_spreadsheet_context(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    intent_state: Optional[Dict] = None,
    source_name: Optional[str] = None,
    destination_name: Optional[str] = None,
    source_sheet_name: Optional[str] = None,
    destination_sheet_name: Optional[str] = None,
) -> SpreadsheetContext:
    """
    Bind spreadsheet context from classification/catalog hints — no prompt LLM detection.
    """
    from services.google_sheets_connection_service import (
        resolve_workbook_by_name,
        resolve_workbook_by_spreadsheet_id,
    )

    workbook_name = (source_name or destination_name or "").strip()
    sheet_name = source_sheet_name or destination_sheet_name

    if workbook_name:
        detection = await resolve_workbook_by_name(
            str(user_id),
            workbook_name,
            sheet_name,
            source="classification",
        )
        if detection.get("found") or detection.get("error") == "ambiguous_spreadsheet_name":
            return _context_from_detection(detection)

    url_pattern = r"https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9-_]+)"
    url_match = re.search(url_pattern, prompt or "")
    if url_match:
        detection = await resolve_workbook_by_spreadsheet_id(
            str(user_id),
            url_match.group(1),
            source="url",
        )
        if detection.get("found") or detection.get("error") == "ambiguous_spreadsheet_name":
            return _context_from_detection(detection)

    prompt_words = (prompt or "").strip().split()
    is_short_clarification = len(prompt_words) <= 5
    if intent_state and intent_state.get("active_source") == "google_sheets" and is_short_clarification:
        pinned = (intent_state.get("source_name") or "").strip()
        if pinned:
            detection = await resolve_workbook_by_name(
                str(user_id),
                pinned,
                source="intent_state",
            )
            if detection.get("found") or detection.get("error") == "ambiguous_spreadsheet_name":
                return _context_from_detection(detection)

    return SpreadsheetContext(
        found=False,
        message=(
            "Which Google Sheet should we use?"
            + (f" (looking for {workbook_name!r})" if workbook_name else "")
        ),
    )
