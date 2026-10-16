"""Tiered search for Google Sheets executor v2."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services.google_sheets.config import gsheets_chat_max_rows
from services.google_sheets.models import SpreadsheetContext
from services.google_sheets_service import GoogleSheetsService

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)


def _extract_literal_tokens(prompt: str) -> List[str]:
    tokens = []
    for m in _EMAIL_RE.finditer(prompt):
        tokens.append(m.group(0))
    for m in _UUID_RE.finditer(prompt):
        tokens.append(m.group(0))
    quoted = re.findall(r'"([^"]+)"|\'([^\']+)\'', prompt)
    for a, b in quoted:
        t = a or b
        if t and len(t) >= 2:
            tokens.append(t)
    return tokens


async def search_spreadsheet(
    sheets_service: GoogleSheetsService,
    ctx: SpreadsheetContext,
    prompt: str,
    max_results: int = 10,
) -> Dict[str, Any]:
    max_rows = gsheets_chat_max_rows()
    result = await sheets_service.read_spreadsheet(
        ctx.spreadsheet_id,
        range_name=f"A1:ZZ{max_rows}",
        sheet_name=ctx.sheet_name,
    )
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "Read failed"), "matching_records": []}

    headers = result.get("headers", ctx.headers) or ctx.headers
    all_records = result.get("data", [])
    if not all_records:
        return {"success": True, "matching_records": [], "search_criteria": {}, "headers": headers}

    literals = _extract_literal_tokens(prompt)
    if literals:
        matches = []
        for record in all_records:
            text = " ".join(str(v).lower() for v in record.values())
            if any(lit.lower() in text for lit in literals):
                matches.append(record)
            if len(matches) >= max_results:
                break
        if matches:
            return {
                "success": True,
                "matching_records": matches,
                "search_criteria": {"method": "literal", "search_terms": literals},
                "headers": headers,
            }

    simple = prompt.strip()
    if len(simple.split()) <= 4 and len(simple) >= 2:
        det = await sheets_service.search_records(
            ctx.spreadsheet_id,
            search_term=simple,
            sheet_name=ctx.sheet_name or "Sheet1",
            max_results=max_results,
        )
        if det.get("success") and det.get("matching_records"):
            return {
                "success": True,
                "matching_records": det["matching_records"],
                "search_criteria": {"method": "deterministic", "search_term": simple},
                "headers": headers,
            }

    return await sheets_service.search_records_with_ai(
        spreadsheet_id=ctx.spreadsheet_id,
        search_query=prompt,
        headers=headers,
        all_records=all_records,
        max_results=max_results,
    )
