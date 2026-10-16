"""Mapping result validation helpers."""
from __future__ import annotations

from typing import Any, Dict


def _normalize_mapping_dict(mapping: Any) -> Dict[str, Any]:
    if not isinstance(mapping, dict):
        return {}
    if "mapping" in mapping and isinstance(mapping.get("mapping"), dict):
        inner = mapping.get("mapping")
        return inner if isinstance(inner, dict) else {}
    return mapping


def _mapping_entry_has_target(entry: Any) -> bool:
    """True when a mapping value points at a destination field/column/object."""
    if not isinstance(entry, dict):
        return False
    for key in ("field", "object", "column"):
        val = entry.get(key)
        if val is not None and str(val).strip():
            return True
    return False


def mapping_config_is_empty(mapping: Any) -> bool:
    """True when no field mappings remain after execution."""
    cfg = _normalize_mapping_dict(mapping)
    if not cfg:
        return True
    for _key, val in cfg.items():
        if _mapping_entry_has_target(val):
            return False
        if isinstance(val, list):
            for item in val:
                if _mapping_entry_has_target(item):
                    return False
    return True
