"""Sanitize SQL field mappings before migration (lookup_of / table names)."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_INTERNAL_SQL_TABLE_NAMES = frozenset(
    {
        "extracted_schemas",
        "sql_extracted_schemas",
        "gsheets_extracted_schemas",
    }
)


def _norm_table(name: str) -> str:
    return (name or "").strip().lower()


def _parse_lookup_of(lookup_of: Optional[str]) -> Optional[Tuple[str, str]]:
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
    return child_table, fk_column


def _resolve_table_key(
    tables_structure: Dict[str, Any],
    table_name: str,
) -> Optional[str]:
    if not table_name:
        return None
    if table_name in tables_structure:
        return table_name
    target = _norm_table(table_name)
    for key in tables_structure:
        if _norm_table(key) == target:
            return key
    return None


def _foreign_keys_for_table(
    tables_structure: Dict[str, Any],
    table_name: str,
) -> List[Dict[str, Any]]:
    key = _resolve_table_key(tables_structure, table_name)
    if not key:
        return []
    meta = tables_structure.get(key) or {}
    fks = meta.get("foreign_keys") or []
    return [fk for fk in fks if isinstance(fk, dict)]


def is_valid_sql_lookup_of(
    mapped_table: Optional[str],
    lookup_of: Optional[str],
    primary_table: Optional[str],
    tables_structure: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when lookup_of is valid for a field mapped to ``mapped_table``.

    Valid patterns (parent = mapped_table, child = table in lookup_of):
    - FK on child references parent: ``child.fk`` → ``parent``
    - Primary owns FK to parent: ``primary.fk`` → ``parent`` (child = primary)
    """
    lo = (lookup_of or "").strip()
    if not lo:
        return False

    parsed = _parse_lookup_of(lo)
    if not parsed:
        return False

    child_table, fk_col = parsed
    mapped = (mapped_table or "").strip()
    primary = (primary_table or "").strip()
    if not mapped:
        return False

    if child_table.lower() in _INTERNAL_SQL_TABLE_NAMES:
        return False

    if _norm_table(mapped) == _norm_table(child_table):
        return False

    if primary and _norm_table(mapped) == _norm_table(primary):
        return False

    if not tables_structure:
        return True

    child_key = _resolve_table_key(tables_structure, child_table)
    if not child_key:
        return False

    fk_norm = _norm_table(fk_col)
    mapped_key = _resolve_table_key(tables_structure, mapped)
    if not mapped_key:
        return False

    for fk in _foreign_keys_for_table(tables_structure, child_key):
        col = (fk.get("column_name") or fk.get("column") or "").strip()
        ref = (fk.get("referenced_table") or "").strip()
        if _norm_table(col) != fk_norm:
            continue
        if _norm_table(ref) == _norm_table(mapped_key):
            return True

    if primary:
        primary_key = _resolve_table_key(tables_structure, primary)
        if primary_key and _norm_table(child_key) == _norm_table(primary_key):
            for fk in _foreign_keys_for_table(tables_structure, primary_key):
                col = (fk.get("column_name") or fk.get("column") or "").strip()
                ref = (fk.get("referenced_table") or "").strip()
                if _norm_table(col) != fk_norm:
                    continue
                if _norm_table(ref) == _norm_table(mapped_key):
                    return True

    return False


def should_drop_sql_lookup_of(
    entry_table: Optional[str],
    lookup_of: Optional[str],
    primary_table: Optional[str] = None,
    *,
    tables_structure: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Return (drop, reason). ``lookup_of`` is only for cross-table relationships
    validated against ``tables_structure`` foreign keys when available.
    """
    lo = (lookup_of or "").strip()
    if not lo:
        return True, "empty"

    parsed = _parse_lookup_of(lo)
    if not parsed:
        return True, "unparseable"

    child_table, _ = parsed
    table = (entry_table or "").strip()

    if child_table.lower() in _INTERNAL_SQL_TABLE_NAMES:
        return True, f"internal_table_ref:{child_table}"

    if table and _norm_table(table) == _norm_table(child_table):
        return True, "same_table_as_lookup_child"

    if primary_table and table and _norm_table(table) == _norm_table(primary_table):
        return True, "primary_table_direct_field"

    if primary_table:
        pt = primary_table.strip().lower()
        if table.lower() == pt and child_table.lower() == pt:
            return True, "primary_table_self_lookup"

    if is_valid_sql_lookup_of(table, lo, primary_table, tables_structure):
        return False, ""

    if tables_structure:
        return True, "invalid_lookup_relationship"

    return False, ""


def _sanitize_mapping_item(
    item: Dict[str, Any],
    primary_table: Optional[str],
    field_key: str,
    index: Optional[int],
    *,
    tables_structure: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Mutates item in place; returns human-readable drop reasons for debug."""
    reasons: List[str] = []
    if not isinstance(item, dict):
        return reasons

    table = (item.get("table") or item.get("object") or item.get("sheet") or "").strip()
    if table.lower() in _INTERNAL_SQL_TABLE_NAMES and primary_table:
        logger.warning(
            "[SQL_MIGRATE_DEBUG] remapped internal table %r -> %r for field %s",
            table,
            primary_table,
            field_key,
        )
        item["table"] = primary_table
        table = primary_table

    lo = item.get("lookup_of")
    drop, reason = should_drop_sql_lookup_of(
        table,
        lo,
        primary_table,
        tables_structure=tables_structure,
    )
    if drop and (lo or "").strip():
        label = f"{field_key}[{index}]" if index is not None else field_key
        reasons.append(f"{label}: dropped lookup_of={lo!r} ({reason})")
        item.pop("lookup_of", None)
    return reasons


def sanitize_sql_mapping_for_migration(
    mapping: Dict[str, Any],
    primary_table: Optional[str],
    *,
    tables_structure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a copy of mapping with invalid lookup_of entries removed."""
    if not mapping or not isinstance(mapping, dict):
        return mapping or {}

    out = deepcopy(mapping)
    dropped: List[str] = []
    for field_key, config in list(out.items()):
        if isinstance(config, list):
            for idx, item in enumerate(config):
                if isinstance(item, dict):
                    dropped.extend(
                        _sanitize_mapping_item(
                            item,
                            primary_table,
                            field_key,
                            idx,
                            tables_structure=tables_structure,
                        )
                    )
        elif isinstance(config, dict):
            dropped.extend(
                _sanitize_mapping_item(
                    config,
                    primary_table,
                    field_key,
                    None,
                    tables_structure=tables_structure,
                )
            )

    for msg in dropped:
        print(f"⚠️ [MAP_SQL] {msg}")
    if dropped:
        print(f"🔧 [MAP_SQL] Sanitized {len(dropped)} invalid lookup_of reference(s)")

    return out


def debug_log_sql_mapping(
    mapping: Dict[str, Any],
    primary_table: Optional[str],
    *,
    dropped_reasons: Optional[List[str]] = None,
) -> None:
    """Structured debug line for migrate-to-database."""
    summary: List[Dict[str, Any]] = []
    lookup_count = 0
    for field_key, config in (mapping or {}).items():
        items = config if isinstance(config, list) else [config]
        for item in items:
            if not isinstance(item, dict):
                continue
            lo = (item.get("lookup_of") or "").strip()
            if lo:
                lookup_count += 1
            summary.append(
                {
                    "field": field_key,
                    "table": item.get("table") or item.get("object"),
                    "column": item.get("column") or item.get("field"),
                    "lookup_of": lo or None,
                }
            )

    payload = {
        "primary_table": primary_table,
        "field_count": len(mapping or {}),
        "entries_with_lookup_of": lookup_count,
        "entries": summary[:25],
        "dropped": dropped_reasons or [],
    }
    logger.info("[SQL_MIGRATE_DEBUG] mapping_payload %s", json.dumps(payload, default=str))
