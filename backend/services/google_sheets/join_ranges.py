"""
In-memory join of two Google Sheets ranges (left join semantics).

Does not write Sheets formulas (no VLOOKUP). Output can be appended to a target sheet via executor.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.google_sheets_service import GoogleSheetsService


async def read_range_as_records(
    sheets_service: GoogleSheetsService,
    spreadsheet_id: str,
    sheet_name: str,
    max_rows: int = 5000,
) -> Tuple[List[str], List[Dict[str, Any]]]:
    result = await sheets_service.read_spreadsheet(
        spreadsheet_id=spreadsheet_id,
        range_name=f"A1:ZZ{max_rows}",
        sheet_name=sheet_name,
    )
    if not result.get("success"):
        raise ValueError(result.get("error") or "Failed to read sheet")
    headers = result.get("headers") or []
    return headers, result.get("data") or []


def _norm_key(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def join_records(
    left_headers: List[str],
    left_rows: List[Dict[str, Any]],
    right_headers: List[str],
    right_rows: List[Dict[str, Any]],
    left_key: str,
    right_key: str,
    how: str = "left",
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Join two record lists on key columns. Right columns are prefixed with 'right_' on collision.
    """
    if left_key not in left_headers:
        raise ValueError(f"Left key column '{left_key}' not in left headers")
    if right_key not in right_headers:
        raise ValueError(f"Right key column '{right_key}' not in right headers")

    right_by_key: Dict[str, List[Dict[str, Any]]] = {}
    for row in right_rows:
        k = _norm_key(row.get(right_key))
        if not k:
            continue
        right_by_key.setdefault(k, []).append(row)

    out_headers = list(left_headers)
    for h in right_headers:
        if h == right_key:
            continue
        prefixed = h if h not in out_headers else f"right_{h}"
        if prefixed not in out_headers:
            out_headers.append(prefixed)

    joined: List[Dict[str, Any]] = []
    matched_right_keys: set = set()

    for left_row in left_rows:
        lk = _norm_key(left_row.get(left_key))
        matches = right_by_key.get(lk, []) if lk else []
        if not matches:
            if how in ("left", "outer"):
                joined.append(dict(left_row))
            continue
        for right_row in matches:
            matched_right_keys.add(lk)
            merged = dict(left_row)
            for h in right_headers:
                if h == right_key:
                    continue
                col = h if h not in merged else f"right_{h}"
                merged[col] = right_row.get(h)
            joined.append(merged)

    if how in ("right", "outer"):
        for row in right_rows:
            rk = _norm_key(row.get(right_key))
            if rk and rk not in matched_right_keys:
                merged = {h: "" for h in out_headers}
                for h in right_headers:
                    col = h if h in merged else f"right_{h}"
                    merged[col] = row.get(h)
                joined.append(merged)

    return out_headers, joined
