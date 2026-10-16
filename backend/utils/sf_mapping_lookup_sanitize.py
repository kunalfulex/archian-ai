"""Sanitize Salesforce field mappings: validate lookup_of against base-object lookup fields."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Set, Tuple


def _parse_lookup_of(lookup_of: Optional[str]) -> Optional[Tuple[str, str]]:
    if not lookup_of or not isinstance(lookup_of, str):
        return None
    s = lookup_of.strip()
    if "." not in s:
        return None
    base_obj, lookup_field = s.rsplit(".", 1)
    base_obj = base_obj.strip()
    lookup_field = lookup_field.strip()
    if not base_obj or not lookup_field:
        return None
    return base_obj, lookup_field


def parse_lookup_of_reference(lookup_of: Any) -> Optional[Tuple[str, str]]:
    """Parse BaseObject.LookupFieldApiName for migration inject_into / inject_field."""
    if lookup_of is None:
        return None
    return _parse_lookup_of(str(lookup_of))


def strip_empty_lookup_of_from_mapping(mapping: Dict[str, Any]) -> Dict[str, Any]:
    """Remove empty or invalid lookup_of keys (panel may persist lookup_of: \"\")."""
    if not mapping or not isinstance(mapping, dict):
        return dict(mapping or {})

    out = deepcopy(mapping)
    for field_key, config in list(out.items()):
        if isinstance(config, list):
            cleaned: List[Any] = []
            for item in config:
                if isinstance(item, dict):
                    item = dict(item)
                    lo = parse_lookup_of_reference(item.get("lookup_of"))
                    if lo:
                        item["lookup_of"] = f"{lo[0]}.{lo[1]}"
                    else:
                        item.pop("lookup_of", None)
                    cleaned.append(item)
                else:
                    cleaned.append(item)
            out[field_key] = cleaned
        elif isinstance(config, dict):
            item = dict(config)
            lo = parse_lookup_of_reference(item.get("lookup_of"))
            if lo:
                item["lookup_of"] = f"{lo[0]}.{lo[1]}"
            else:
                item.pop("lookup_of", None)
            out[field_key] = item
    return out


def _norm_object(name: str) -> str:
    return (name or "").strip().lower()


def build_base_lookup_index(
    objects_structure: Dict[str, Any],
    base_objects: Optional[List[str]] = None,
) -> Tuple[Set[str], Dict[str, str]]:
    """
    Build valid lookup_of references from synced org metadata.

    Returns:
        valid_lookup_refs: ``{"Contact.AccountId", ...}`` (canonical API names)
        lookup_ref_targets: lookup_ref -> referenced object API name
    """
    valid_refs: Set[str] = set()
    ref_targets: Dict[str, str] = {}
    bases = [b for b in (base_objects or []) if (b or "").strip()]
    if not bases and objects_structure:
        bases = list(objects_structure.keys())

    for base_obj in bases:
        meta = (objects_structure or {}).get(base_obj) or {}
        fields = meta.get("fields") or {}
        if not isinstance(fields, dict):
            continue
        for field_name, field_info in fields.items():
            if not isinstance(field_info, dict):
                continue
            if not field_info.get("is_lookup"):
                continue
            target = (field_info.get("lookup_object") or "").strip()
            if not target:
                continue
            ref = f"{base_obj}.{field_name}"
            valid_refs.add(ref)
            ref_targets[ref] = target
            ref_targets[_norm_object(ref)] = target

    return valid_refs, ref_targets


def should_drop_salesforce_lookup_of(
    mapped_object: Optional[str],
    lookup_of: Optional[str],
    *,
    base_objects: Optional[List[str]] = None,
    objects_structure: Optional[Dict[str, Any]] = None,
    valid_lookup_refs: Optional[Set[str]] = None,
    lookup_ref_targets: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """
    Return (drop, reason). ``lookup_of`` is only valid for fields on lookup objects
    when it references a real lookup field on a migration base object pointing at
    ``mapped_object``.
    """
    lo = (lookup_of or "").strip()
    if not lo:
        return False, ""

    parsed = _parse_lookup_of(lo)
    if not parsed:
        return True, "unparseable"

    lo_base, lo_field = parsed
    target_obj = (mapped_object or "").strip()
    if not target_obj:
        return True, "missing_mapped_object"

    bases = [b for b in (base_objects or []) if (b or "").strip()]
    base_norms = {_norm_object(b) for b in bases}

    # Direct fields on the migration base object are inserted/updated — not lookup children.
    if base_norms and _norm_object(target_obj) in base_norms:
        return True, "primary_object_direct_field"

    if base_norms and _norm_object(lo_base) not in base_norms:
        return True, "lookup_base_not_migration_base"

    if valid_lookup_refs is None or lookup_ref_targets is None:
        valid_lookup_refs, lookup_ref_targets = build_base_lookup_index(
            objects_structure or {}, bases
        )

    canonical_ref = f"{lo_base}.{lo_field}"
    if canonical_ref not in valid_lookup_refs:
        # Case-insensitive field match on the same base object
        matched_ref = None
        for ref in valid_lookup_refs:
            ref_base, ref_field = ref.rsplit(".", 1)
            if _norm_object(ref_base) == _norm_object(lo_base) and ref_field.lower() == lo_field.lower():
                matched_ref = ref
                break
        if not matched_ref:
            return True, "invalid_lookup_field"
        canonical_ref = matched_ref

    expected_target = lookup_ref_targets.get(canonical_ref) or lookup_ref_targets.get(
        _norm_object(canonical_ref)
    )
    if not expected_target:
        return True, "invalid_lookup_field"

    if _norm_object(expected_target) != _norm_object(target_obj):
        return True, "lookup_target_mismatch"

    return False, ""


def _sanitize_entry(
    item: Dict[str, Any],
    *,
    base_objects: Optional[List[str]],
    objects_structure: Dict[str, Any],
    valid_lookup_refs: Set[str],
    lookup_ref_targets: Dict[str, str],
    field_key: str,
    index: Optional[int],
) -> List[str]:
    reasons: List[str] = []
    if not isinstance(item, dict):
        return reasons

    lo = item.get("lookup_of")
    if not (lo or "").strip():
        item.pop("lookup_of", None)
        return reasons
    if not parse_lookup_of_reference(lo):
        label = f"{field_key}[{index}]" if index is not None else field_key
        mapped_object = (item.get("object") or "").strip()
        dest = f"{mapped_object}.{item.get('field')}" if mapped_object else field_key
        reasons.append(f"{label} → {dest}: dropped lookup_of={lo!r} (invalid_format)")
        item.pop("lookup_of", None)
        return reasons

    mapped_object = (item.get("object") or "").strip()
    drop, reason = should_drop_salesforce_lookup_of(
        mapped_object,
        lo,
        base_objects=base_objects,
        objects_structure=objects_structure,
        valid_lookup_refs=valid_lookup_refs,
        lookup_ref_targets=lookup_ref_targets,
    )
    if drop:
        label = f"{field_key}[{index}]" if index is not None else field_key
        dest = f"{mapped_object}.{item.get('field')}" if mapped_object else field_key
        reasons.append(f"{label} → {dest}: dropped lookup_of={lo!r} ({reason})")
        item.pop("lookup_of", None)
    return reasons


def sanitize_salesforce_mapping_lookup_of(
    mapping: Dict[str, Any],
    objects_structure: Dict[str, Any],
    base_objects: Optional[List[str]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return mapping copy with invalid lookup_of removed; list of drop reasons."""
    if not mapping or not isinstance(mapping, dict):
        return dict(mapping or {}), []

    out = deepcopy(mapping)
    valid_refs, ref_targets = build_base_lookup_index(objects_structure, base_objects)
    dropped: List[str] = []

    for field_key, config in out.items():
        if isinstance(config, list):
            for idx, item in enumerate(config):
                if isinstance(item, dict):
                    dropped.extend(
                        _sanitize_entry(
                            item,
                            base_objects=base_objects,
                            objects_structure=objects_structure,
                            valid_lookup_refs=valid_refs,
                            lookup_ref_targets=ref_targets,
                            field_key=field_key,
                            index=idx,
                        )
                    )
        elif isinstance(config, dict):
            dropped.extend(
                _sanitize_entry(
                    config,
                    base_objects=base_objects,
                    objects_structure=objects_structure,
                    valid_lookup_refs=valid_refs,
                    lookup_ref_targets=ref_targets,
                    field_key=field_key,
                    index=None,
                )
            )

    for msg in dropped:
        print(f"⚠️ [MAP_SF] {msg}")

    if dropped:
        print(f"🔧 [MAP_SF] Sanitized {len(dropped)} invalid lookup_of reference(s)")

    return out, dropped
