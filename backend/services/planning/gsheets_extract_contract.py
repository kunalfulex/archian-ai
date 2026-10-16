"""Structured extract contract for Google Sheets pulls."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_gsheets_extract_contract(
    classification: Dict[str, Any],
    *,
    migration_intent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins spreadsheet, tab, and optional column scope for extract_google_sheets."""
    mi = dict(migration_intent or classification.get("migration_intent") or {})
    headers: List[str] = []
    for key in ("field_hints", "selected_fields", "columns"):
        raw = mi.get(key)
        if isinstance(raw, list):
            headers.extend(str(h).strip() for h in raw if str(h).strip())

    contract: Dict[str, Any] = {
        "spreadsheet_id": (classification.get("source_spreadsheet_id") or "").strip() or None,
        "spreadsheet_name": (classification.get("source_name") or "").strip() or None,
        "sheet_name": (classification.get("source_sheet_name") or "").strip() or None,
        "entity_key": (
            (classification.get("source_object_api_name") or "").strip()
            or (classification.get("source_sheet_name") or "").strip()
            or None
        ),
        "column_headers": headers or None,
        "limit": classification.get("extract_limit"),
    }
    if mi.get("extract_field_scope"):
        contract["extract_field_scope"] = mi.get("extract_field_scope")
    return {k: v for k, v in contract.items() if v is not None}
