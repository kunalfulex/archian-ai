"""Build persisted extraction_metadata from plan / classification intent."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _as_dict(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return dict(value)
    return {}


def _unique_strs(items: Any) -> List[str]:
    out: List[str] = []
    for raw in items or []:
        s = str(raw).strip()
        if s and s not in out:
            out.append(s)
    return out


def build_persisted_extraction_metadata(
    *,
    migration_intent: Optional[Any] = None,
    extract_field_scope: Optional[Dict[str, Any]] = None,
    related_entity_keys: Optional[List[str]] = None,
    source_entity_key: Optional[str] = None,
    field_hints: Optional[List[str]] = None,
    classification: Optional[Dict[str, Any]] = None,
    destination: Optional[str] = None,
    destination_name: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Merge intent + scope into extraction_metadata for DB and extraction_completed events.

    Callers pass whatever context they have; later keys win via merge order.
    """
    meta: Dict[str, Any] = {}
    cls = dict(classification or {})
    mi = _as_dict(migration_intent)
    scope = dict(extract_field_scope or {})

    if cls.get("migration_intent") and not mi:
        mi = _as_dict(cls.get("migration_intent"))

    entity = (
        (source_entity_key or "").strip()
        or (mi.get("driving_entity_key") or "").strip()
        or (cls.get("source_object_api_name") or "").strip()
        or (cls.get("source_table_name") or cls.get("source_table") or "").strip()
        or (cls.get("source_sheet_name") or "").strip()
        or (scope.get("source_entity_key") or "").strip()
    )
    if entity:
        meta["source_entity_key"] = entity
        meta.setdefault("object_api_name", entity)

    hints = _unique_strs(field_hints or mi.get("field_hints") or scope.get("field_hints"))
    if hints:
        meta["field_hints"] = hints

    scope_val = (
        (mi.get("extract_field_scope") or "").strip()
        or (scope.get("extract_field_scope") or "").strip()
        or (cls.get("extract_field_scope") or "").strip()
    )
    if scope_val:
        meta["extract_field_scope"] = scope_val

    include_related = bool(
        mi.get("include_related_data")
        or scope.get("include_related_data")
        or related_entity_keys
    )
    if include_related:
        meta["include_related_data"] = True

    rel_keys = _unique_strs(
        related_entity_keys
        or mi.get("related_entity_keys")
        or scope.get("related_entity_keys")
    )
    if rel_keys:
        meta["related_entity_keys"] = rel_keys

    parent = (mi.get("parent_entity_key") or mi.get("related_entity_key") or "").strip()
    if not parent and rel_keys:
        parent = rel_keys[0]
    if not parent:
        parents = scope.get("parent_entity_keys") or []
        if parents:
            parent = str(parents[0]).strip()
    if parent:
        meta["parent_entity_key"] = parent
        if not meta.get("related_entity_keys"):
            meta["related_entity_keys"] = [parent]

    parent_label = (mi.get("parent_entity_label") or mi.get("related_entity_label") or "").strip()
    if parent_label:
        meta["parent_entity_label"] = parent_label

    related_hints = _unique_strs(
        mi.get("related_field_hints") or scope.get("related_field_hints")
    )
    if related_hints:
        meta["related_field_hints"] = related_hints

    for key in (
        "lookup_field_path",
        "lookup_relationship_name",
        "driving_entity_key",
        "driving_entity_label",
        "extraction_summary",
        "field_hints_strict",
    ):
        val = mi.get(key) or scope.get(key)
        if val is not None and val != "" and val != []:
            meta[key] = val

    dest = (destination or cls.get("destination") or "").strip()
    dest_name = (destination_name or cls.get("destination_name") or "").strip()
    if dest and dest not in ("unknown", ""):
        meta["destination"] = dest
    if dest_name:
        meta["destination_name"] = dest_name

    if extra:
        for k, v in extra.items():
            if v is not None and v != "" and v != []:
                meta[k] = v

    return meta
