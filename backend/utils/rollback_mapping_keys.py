"""Derive rollback match keys from migration batch mapping_config (all destinations)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

_MAPPING_CONFIG_META_KEYS = frozenset(
    {
        "document_type",
        "spreadsheet_id",
        "sheet_name",
        "spreadsheet_name",
        "operation",
        "field_count",
        "field_names",
        "connector_id",
        "table_name",
        "destination_name",
        "org_id",
        "connected_app_id",
        "objects_involved",
        "mapping",
        "mapping_entries",
        "key_columns",
        "lookup_migration",
        "lookup_tables_order",
        "headers",
    }
)


def _dedupe_preserve(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        key = (item or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def user_mapping_from_config(mapping_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(mapping_config, dict):
        return {}
    nested = mapping_config.get("mapping")
    if isinstance(nested, dict) and nested:
        return nested
    return {
        k: v
        for k, v in mapping_config.items()
        if k not in _MAPPING_CONFIG_META_KEYS and not str(k).startswith("_")
    }


def duplicate_check_columns_from_mapping(
    mapping: Dict[str, Any],
    *,
    destination: Optional[str] = None,
) -> List[str]:
    """Destination column/field names flagged with is_duplicate_check in the panel mapping."""
    if not isinstance(mapping, dict):
        return []
    dest = (destination or "").lower()
    cols: List[str] = []
    for _src, cfg in mapping.items():
        items = cfg if isinstance(cfg, list) else [cfg]
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("is_duplicate_check"):
                continue
            if dest in ("google_sheets", "gsheets"):
                col = item.get("column") or item.get("field")
            elif dest == "sql":
                col = item.get("column") or item.get("field")
            else:
                col = item.get("field") or item.get("column")
            if col:
                cols.append(str(col))
    return _dedupe_preserve(cols)


def extract_key_columns_from_mapping_config(
    mapping_config: Optional[Dict[str, Any]],
    *,
    destination: Optional[str] = None,
    sample_row: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """
    Resolve columns/fields used to locate created rows during rollback.
    Prefers explicit key_columns saved on the batch, then mapping_entries, then is_duplicate_check flags.
    """
    if not isinstance(mapping_config, dict):
        mapping_config = {}

    explicit = mapping_config.get("key_columns")
    if isinstance(explicit, list) and explicit:
        return _dedupe_preserve([str(c) for c in explicit if c])

    entries = mapping_config.get("mapping_entries")
    if isinstance(entries, list):
        from_entries = [
            str(e.get("column") or e.get("field") or "")
            for e in entries
            if isinstance(e, dict) and e.get("is_duplicate_check")
        ]
        from_entries = _dedupe_preserve(from_entries)
        if from_entries:
            return from_entries

    user_map = user_mapping_from_config(mapping_config)
    from_flags = duplicate_check_columns_from_mapping(user_map, destination=destination)
    if from_flags:
        return from_flags

    if isinstance(sample_row, dict) and sample_row:
        from services.google_sheets.row_builder import infer_key_columns

        headers_guess = list(sample_row.keys())
        inferred = infer_key_columns(headers_guess)
        if inferred:
            return inferred

    return []


def build_mapping_config_for_batch(
    *,
    mapping: Dict[str, Any],
    destination: str,
    base: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge user mapping + duplicate-check keys into batch mapping_config for rollback."""
    out = dict(base or {})
    dest = (destination or "").lower()
    user_map = dict(mapping or {})
    key_columns = duplicate_check_columns_from_mapping(user_map, destination=dest)
    out["mapping"] = user_map
    if key_columns:
        out["key_columns"] = key_columns
    return out
