"""Build sheet rows from records and mapping entries."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.data_type_converter import convert_value_for_sheets


def identity_mapping_entries(headers: List[str]) -> List[Dict[str, Any]]:
    return [
        {
            "field": h,
            "column": h,
            "position": i,
            "is_duplicate_check": False,
        }
        for i, h in enumerate(headers)
    ]


def build_row_values(
    record: Dict[str, Any],
    mapping_entries: List[Dict[str, Any]],
    headers: List[str],
    header_index: Dict[str, int],
    base_values: Optional[List[str]] = None,
) -> List[str]:
    row_values = list(base_values) if base_values is not None else [""] * len(headers)

    for entry in mapping_entries:
        column = entry["column"]
        idx = header_index.get(column)
        if idx is None or idx >= len(row_values):
            continue

        value = record.get(entry["field"], "")

        if base_values is not None:
            if value is None or (isinstance(value, str) and value.strip() == ""):
                continue

        try:
            converted_value = convert_value_for_sheets(value, column_name=column)
            if converted_value is None:
                if base_values is None:
                    row_values[idx] = ""
            elif isinstance(converted_value, bool):
                row_values[idx] = str(converted_value).lower() if converted_value else "FALSE"
            elif isinstance(converted_value, (int, float)):
                row_values[idx] = str(converted_value)
            else:
                row_values[idx] = str(converted_value)
        except Exception:
            if base_values is None or (value is not None and str(value).strip() != ""):
                row_values[idx] = "" if value is None else str(value)

    return row_values


def infer_key_columns(headers: List[str], preferred: Optional[List[str]] = None) -> List[str]:
    if preferred:
        return [c for c in preferred if c in headers]
    priority = ("email", "e-mail", "id", "uuid", "external id", "record id", "name")
    lowered = {h.lower(): h for h in headers}
    for key in priority:
        for h_lower, h_orig in lowered.items():
            if key in h_lower:
                return [h_orig]
    return []
