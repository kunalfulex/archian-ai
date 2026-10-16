"""Resolve destination entity API keys vs catalog document labels."""
from __future__ import annotations

from typing import Any, Dict, Optional


def looks_like_catalog_document_label(value: Optional[str]) -> bool:
    """True when value is a human document category, not an object/table API name."""
    s = (value or "").strip()
    if not s:
        return False
    if " " in s:
        return True
    lower = s.lower()
    if lower.startswith("salesforce ") or lower.endswith(" data"):
        return True
    return False


def infer_salesforce_object_from_mapping(
    mapping: Optional[Dict[str, Any]],
) -> Optional[str]:
    """Majority-vote primary Salesforce object from field mapping entries."""
    if not isinstance(mapping, dict) or not mapping:
        return None
    counts: Dict[str, int] = {}
    for spec in mapping.values():
        items = spec if isinstance(spec, list) else [spec]
        for item in items:
            if not isinstance(item, dict):
                continue
            obj = (item.get("object") or "").strip()
            if obj:
                counts[obj] = counts.get(obj, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def coerce_destination_entity_api_name(
    value: Optional[str],
    *,
    connector_type: str = "salesforce",
    mapping: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Return a connector entity API key.

    Catalog labels (e.g. ``Salesforce Contact Data``) are rejected; mapping is used
    to infer the real object/table when possible.
    """
    raw = (value or "").strip()
    if raw and not looks_like_catalog_document_label(raw):
        return raw

    st = (connector_type or "").strip().lower()
    if st == "salesforce":
        return infer_salesforce_object_from_mapping(mapping)
    if st == "sql":
        from services.migration.session_migration import _infer_sql_table_from_mapping

        return _infer_sql_table_from_mapping(mapping)
    return None
