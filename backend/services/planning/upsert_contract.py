"""Structured upsert contract from PLAN_SCOPE + classification."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.plan_scope import read_plan_scope


def _normalize_keys(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def build_upsert_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins target sheet/workbook and key columns for gsheets_execute_operation (upsert)."""
    g = dict(goal or read_plan_scope(classification))
    mi = dict(classification.get("migration_intent") or {})
    keys = _normalize_keys(
        classification.get("upsert_key_columns")
        or g.get("upsert_key_columns")
        or mi.get("upsert_key_columns")
        or mi.get("field_hints")
    )
    return {
        "operation": "upsert",
        "destination_name": (classification.get("destination_name") or "").strip() or None,
        "destination_sheet_name": (classification.get("destination_sheet_name") or "").strip() or None,
        "destination_spreadsheet_id": (classification.get("destination_spreadsheet_id") or "").strip() or None,
        "key_columns": keys or None,
        "entity_key": (
            (classification.get("destination_object_api_name") or "").strip()
            or (classification.get("destination_sheet_name") or "").strip()
            or None
        ),
    }
