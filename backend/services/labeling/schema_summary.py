"""Build connector-native schema payloads for entity labeling."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from db.extracted_schema import _parse_gsheets_header_to_columns, _sql_schema_data_to_foreign_keys


def _parse_schema_data(raw: Any) -> Dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _normalize_sql_columns(schema_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cols = schema_data.get("columns")
    if isinstance(cols, list):
        for col in cols:
            if not isinstance(col, dict):
                continue
            name = (col.get("column_name") or col.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "type": col.get("data_type") or col.get("type"),
                    "nullable": col.get("is_nullable", col.get("nullable")),
                    "primary_key": bool(col.get("is_primary_key", False)),
                }
            )
    return out


def relationship_hints_from_sql_foreign_keys(
    foreign_keys: List[Dict[str, Any]],
    *,
    catalog_tables: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Derive relationship_hints from sql_extracted_schemas.schema_data.foreign_keys."""
    catalog_lower = {(t or "").strip().lower() for t in (catalog_tables or set()) if t}
    hints: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for fk in foreign_keys or []:
        if not isinstance(fk, dict):
            continue
        col = (fk.get("column_name") or fk.get("column") or "").strip()
        parent = (fk.get("referenced_table") or fk.get("foreign_table_name") or "").strip()
        if not col or not parent:
            continue
        if catalog_lower and parent.lower() not in catalog_lower:
            continue
        key = f"{parent.lower()}:{col.lower()}"
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            {
                "related_entity": parent,
                "relationship": "lookup",
                "field": col,
            }
        )
    return hints


def build_sql_labeling_schema_summary(
    *,
    table_name: str,
    schema_data_raw: Any,
    catalog_tables: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Full structural schema from sql_extracted_schemas.schema_data for labeling."""
    schema_data = _parse_schema_data(schema_data_raw)
    foreign_keys = _sql_schema_data_to_foreign_keys(schema_data)
    catalog = sorted({(t or "").strip() for t in (catalog_tables or []) if (t or "").strip()})
    catalog_set = set(catalog)
    suggested = relationship_hints_from_sql_foreign_keys(foreign_keys, catalog_tables=catalog_set)

    summary: Dict[str, Any] = {
        "table_name": table_name,
        "columns": _normalize_sql_columns(schema_data),
        "foreign_keys": foreign_keys,
        "catalog_tables": catalog,
        "suggested_relationship_hints": suggested,
    }
    for key in ("primary_keys", "indexes", "constraints"):
        if key in schema_data and schema_data[key]:
            summary[key] = schema_data[key]
    return summary


def relationship_hints_from_salesforce_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if not field.get("is_lookup"):
            continue
        parent = (field.get("lookup_object_api_name") or "").strip()
        col = (field.get("field_api_name") or "").strip()
        if not parent or not col:
            continue
        key = f"{parent.lower()}:{col.lower()}"
        if key in seen:
            continue
        seen.add(key)
        hints.append(
            {
                "related_entity": parent,
                "relationship": "lookup",
                "field": col,
            }
        )
    return hints


def build_salesforce_labeling_schema_summary(
    *,
    object_api_name: str,
    label: Optional[str],
    fields: List[Dict[str, Any]],
) -> Dict[str, Any]:
    field_dicts = [dict(f) for f in fields or [] if isinstance(f, dict)]
    suggested = relationship_hints_from_salesforce_fields(field_dicts)
    return {
        "object_api_name": object_api_name,
        "label": label or object_api_name,
        "fields": field_dicts,
        "suggested_relationship_hints": suggested,
    }


def _normalize_gsheets_headers(header_schema_raw: Any) -> List[Dict[str, Any]]:
    if header_schema_raw is None:
        return []
    if isinstance(header_schema_raw, str):
        try:
            header_schema_raw = json.loads(header_schema_raw)
        except json.JSONDecodeError:
            return []
    headers: List[Dict[str, Any]] = []
    if isinstance(header_schema_raw, dict) and header_schema_raw.get("headers"):
        for h in header_schema_raw.get("headers") or []:
            if isinstance(h, dict):
                name = (h.get("name") or h.get("column_name") or "").strip()
                if name:
                    headers.append(
                        {
                            "name": name,
                            "type": h.get("type") or h.get("data_type") or "text",
                            "position": h.get("position"),
                            "description": (h.get("description") or "").strip() or None,
                        }
                    )
            elif isinstance(h, str) and h.strip():
                headers.append({"name": h.strip(), "type": "text"})
    else:
        for name, meta in _parse_gsheets_header_to_columns(header_schema_raw).items():
            headers.append(
                {
                    "name": name,
                    "type": (meta.get("data_type") if isinstance(meta, dict) else None) or "text",
                    "description": (meta.get("description") if isinstance(meta, dict) else None),
                }
            )
    return headers


def build_gsheets_labeling_schema_summary(
    *,
    spreadsheet_id: str,
    spreadsheet_name: Optional[str],
    sheet_name: str,
    entity_key: str,
    header_schema_raw: Any,
    sibling_tabs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    headers = _normalize_gsheets_headers(header_schema_raw)
    siblings = list(sibling_tabs or [])
    return {
        "entity_key": entity_key,
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_name": spreadsheet_name,
        "sheet_name": sheet_name,
        "headers": headers,
        "sibling_tabs": siblings,
        "suggested_relationship_hints": [],
    }


def filter_relationship_hints_to_catalog(
    hints: List[Dict[str, Any]],
    *,
    allowed_entity_keys: Set[str],
) -> List[Dict[str, Any]]:
    """Keep only hints whose related_entity matches a known catalog entity_key."""
    allowed_lower = {(k or "").strip().lower() for k in allowed_entity_keys if k}
    out: List[Dict[str, Any]] = []
    for raw in hints or []:
        if not isinstance(raw, dict):
            continue
        related = (raw.get("related_entity") or "").strip()
        if not related:
            continue
        if allowed_lower and related.lower() not in allowed_lower:
            continue
        out.append(
            {
                "related_entity": related,
                "relationship": (raw.get("relationship") or "lookup").strip() or "lookup",
                "field": (raw.get("field") or "").strip() or None,
            }
        )
    return out
