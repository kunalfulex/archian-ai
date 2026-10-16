"""Generic split helpers: positional split with monotoken → required output slot."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple


def destination_field_is_required_from_schema(
    objects_structure: Optional[Dict[str, Any]],
    container: str,
    field_name: str,
) -> bool:
    """Read ``is_required`` from Salesforce/SQL/GSheet structure when present."""
    if not objects_structure or not container or not field_name:
        return False
    meta = objects_structure.get(container)
    if not isinstance(meta, dict):
        return False
    fields = meta.get("fields") or meta.get("columns") or {}
    if not isinstance(fields, dict):
        return False
    spec = fields.get(field_name)
    if isinstance(spec, dict):
        return bool(spec.get("is_required", False))
    return False


def resolve_split_required_indices(
    output_fields: Sequence[str],
    *,
    mapping_items: Optional[Sequence[Dict[str, Any]]] = None,
    field_metadata: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None,
    metadata_keys: Optional[Sequence[Optional[Tuple[str, str]]]] = None,
    rule: Optional[Dict[str, Any]] = None,
) -> List[int]:
    """
    Indices into ``output_fields`` that are required (for monotoken placement).

    Priority per index: mapping entry ``is_required`` > destination metadata
    ``is_required`` > rule ``required_output_fields`` list.
    """
    if not output_fields:
        return []

    required: List[int] = []
    rule_required = set()
    if isinstance(rule, dict):
        for name in rule.get("required_output_fields") or []:
            if name:
                rule_required.add(str(name).strip().lower())

    for idx, out_name in enumerate(output_fields):
        is_req = False

        if mapping_items and idx < len(mapping_items):
            item = mapping_items[idx]
            if isinstance(item, dict) and item.get("is_required"):
                is_req = True

        if not is_req and field_metadata and metadata_keys and idx < len(metadata_keys):
            key = metadata_keys[idx]
            if key and isinstance(field_metadata.get(key), dict):
                is_req = bool(field_metadata[key].get("is_required", False))

        if not is_req and out_name and str(out_name).strip().lower() in rule_required:
            is_req = True

        if is_req:
            required.append(idx)

    return required


def split_value_into_parts(
    value: str,
    output_fields: Sequence[str],
    *,
    split_char: str = " ",
    required_indices: Optional[Sequence[int]] = None,
) -> List[str]:
    """
    Split ``value`` into ``len(output_fields)`` parts.

    Multi-token: standard positional split on ``split_char``.

    Single token: place in the first **required** output index when
    ``required_indices`` is non-empty; otherwise in index 0.
    """
    output_fields = list(output_fields or [])
    num_parts = len(output_fields)
    if num_parts == 0:
        return []

    text = (value or "").strip() if isinstance(value, str) else str(value or "").strip()
    if not text:
        return [""] * num_parts

    raw_parts = text.split(split_char, num_parts - 1)
    while len(raw_parts) < num_parts:
        raw_parts.append("")
    parts = [part.strip() for part in raw_parts[:num_parts]]

    tokens = text.split()
    if len(tokens) == 1:
        token = tokens[0]
        req = [i for i in (required_indices or []) if 0 <= i < num_parts]
        target_idx = req[0] if req else 0
        parts = [""] * num_parts
        parts[target_idx] = token

    return parts


def metadata_keys_for_split_mapping(
    mapping_items: Sequence[Dict[str, Any]],
    output_fields: Sequence[str],
    *,
    destination: str,
) -> List[Optional[Tuple[str, str]]]:
    """Build (container, field/column) keys aligned with split output indices."""
    keys: List[Optional[Tuple[str, str]]] = []
    dest = (destination or "").lower()
    for idx, _out in enumerate(output_fields):
        if idx >= len(mapping_items) or not isinstance(mapping_items[idx], dict):
            keys.append(None)
            continue
        item = mapping_items[idx]
        if dest == "salesforce":
            obj = item.get("object")
            field = item.get("field")
            keys.append((obj, field) if obj and field else None)
        elif dest in ("google_sheets", "gsheets"):
            sheet = item.get("sheet")
            col = item.get("column")
            keys.append((sheet, col) if sheet and col else None)
        elif dest in ("sql", "database"):
            table = item.get("table")
            col = item.get("column")
            keys.append((table, col) if table and col else None)
        else:
            keys.append(None)
    return keys
