"""
Shared context for LLM mapping transform analysis (split/combine decisions).

The transform LLM should decide split vs combine; this module supplies structured
hints (unmapped sources, destination name-column shape) without hardcoding outcomes.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

_GENERIC_NAME_COLUMNS = frozenset(
    {
        "name",
        "full_name",
        "fullname",
        "display_name",
        "displayname",
        "person_name",
        "personname",
        "contact_name",
        "contactname",
        "customer_name",
        "customername",
    }
)

_FIRST_NAME_ALIASES = frozenset(
    {
        "first_name",
        "firstname",
        "first name",
        "fname",
        "given_name",
        "givenname",
        "forename",
    }
)

_LAST_NAME_ALIASES = frozenset(
    {
        "last_name",
        "lastname",
        "last name",
        "lname",
        "family_name",
        "familyname",
        "surname",
    }
)


def _norm_field_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (key or "").lower()).strip()


def _compact(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (key or "").lower())


def is_likely_first_name_source(key: str) -> bool:
    n = _norm_field_key(key).replace(" ", "_")
    c = _compact(key)
    if n in _FIRST_NAME_ALIASES or c in {a.replace(" ", "") for a in _FIRST_NAME_ALIASES}:
        return True
    return n.startswith("first") and "name" in n


def is_likely_last_name_source(key: str) -> bool:
    n = _norm_field_key(key).replace(" ", "_")
    c = _compact(key)
    if n in _LAST_NAME_ALIASES or c in {a.replace(" ", "") for a in _LAST_NAME_ALIASES}:
        return True
    return n.startswith("last") and "name" in n


def is_likely_compound_name_source(key: str) -> bool:
    if is_likely_first_name_source(key) or is_likely_last_name_source(key):
        return False
    n = _norm_field_key(key)
    c = _compact(key)
    if c in _GENERIC_NAME_COLUMNS:
        return True
    tokens = set(n.split())
    return bool(tokens & {"full", "display", "contact", "customer", "person", "employee"}) and "name" in tokens


def classify_source_name_fields(flat_data: Dict[str, Any]) -> Dict[str, List[str]]:
    first_keys: List[str] = []
    last_keys: List[str] = []
    compound_keys: List[str] = []
    for key in (flat_data or {}).keys():
        if is_likely_first_name_source(key):
            first_keys.append(key)
        elif is_likely_last_name_source(key):
            last_keys.append(key)
        elif is_likely_compound_name_source(key):
            compound_keys.append(key)
    return {
        "first_name_sources": first_keys,
        "last_name_sources": last_keys,
        "compound_name_sources": compound_keys,
    }


def _column_names_from_structure(columns_meta: Any) -> List[str]:
    if isinstance(columns_meta, dict):
        return [str(k) for k in columns_meta.keys() if k]
    if isinstance(columns_meta, list):
        out: List[str] = []
        for item in columns_meta:
            if isinstance(item, dict) and item.get("name"):
                out.append(str(item["name"]))
            elif isinstance(item, str):
                out.append(item)
        return out
    return []


def classify_destination_name_columns(column_names: List[str]) -> Dict[str, Any]:
    lower_to_canon: Dict[str, str] = {}
    for name in column_names:
        lower_to_canon[str(name).strip().lower()] = str(name).strip()

    first_col = None
    for alias in _FIRST_NAME_ALIASES:
        if alias in lower_to_canon:
            first_col = lower_to_canon[alias]
            break

    last_col = None
    for alias in _LAST_NAME_ALIASES:
        if alias in lower_to_canon:
            last_col = lower_to_canon[alias]
            break

    generic_cols: List[str] = []
    for low, canon in lower_to_canon.items():
        if low in _GENERIC_NAME_COLUMNS:
            generic_cols.append(canon)

    has_separate = bool(first_col and last_col and first_col.lower() != last_col.lower())
    return {
        "first_name_column": first_col,
        "last_name_column": last_col,
        "generic_name_columns": generic_cols,
        "has_separate_first_last": has_separate,
        "name_only_destination": bool(generic_cols) and not has_separate,
    }


def classify_gsheets_destination_name_shape(sheets_structure: Dict[str, Any]) -> Dict[str, Any]:
    per_sheet: Dict[str, Any] = {}
    for sheet_name, meta in (sheets_structure or {}).items():
        if not isinstance(meta, dict):
            continue
        cols = _column_names_from_structure(meta.get("columns") or {})
        per_sheet[sheet_name] = classify_destination_name_columns(cols)
    return per_sheet


def classify_sql_destination_name_shape(tables_structure: Dict[str, Any]) -> Dict[str, Any]:
    per_table: Dict[str, Any] = {}
    for table_name, meta in (tables_structure or {}).items():
        if not isinstance(meta, dict):
            continue
        cols = _column_names_from_structure(meta.get("columns") or {})
        per_table[table_name] = classify_destination_name_columns(cols)
    return per_table


def classify_salesforce_destination_name_shape(objects_structure: Dict[str, Any]) -> Dict[str, Any]:
    per_object: Dict[str, Any] = {}
    for obj_name, meta in (objects_structure or {}).items():
        if not isinstance(meta, dict):
            continue
        fields = meta.get("fields") or {}
        if isinstance(fields, dict):
            cols = list(fields.keys())
        else:
            cols = []
        per_object[obj_name] = classify_destination_name_columns(cols)
    return per_object


def unmapped_source_fields(flat_data: Dict[str, Any], mapping: Dict[str, Any]) -> List[str]:
    mapped = set((mapping or {}).keys())
    return [k for k in (flat_data or {}).keys() if k not in mapped]


def build_transform_llm_context_section(
    *,
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str = "google_sheets",
) -> str:
    """
    Extra prompt block: full source field list, unmapped fields, name-field hints.
    LLM remains the decision maker; this prevents omitting first/last from analysis.
    """
    source_names = classify_source_name_fields(flat_data or {})
    unmapped = unmapped_source_fields(flat_data or {}, mapping or {})
    unmapped_name_related = [
        k
        for k in unmapped
        if is_likely_first_name_source(k)
        or is_likely_last_name_source(k)
        or is_likely_compound_name_source(k)
    ]

    all_source_keys = list((flat_data or {}).keys())
    lines = [
        "📌 **Full Source Field List (all extracted columns — use for combine/split decisions):**",
        json.dumps(all_source_keys, indent=2),
        "",
        "📌 **Source name-field classification (hints only — you decide split vs combine):**",
        json.dumps(source_names, indent=2),
    ]

    if unmapped:
        lines.extend(
            [
                "",
                "📌 **Unmapped source fields (NOT in Current Mapping — still evaluate for combine):**",
                json.dumps(unmapped, indent=2),
            ]
        )
    if unmapped_name_related:
        lines.extend(
            [
                "",
                "📌 **Unmapped name-related sources (high priority for combine if dest has single Name column):**",
                json.dumps(unmapped_name_related, indent=2),
            ]
        )

    lines.extend(
        [
            "",
            f"📌 **Destination name-column shape ({destination_type}):**",
            json.dumps(destination_name_shape, indent=2),
            "",
            "**LLM decision guidance (not hardcoded rules):**",
            "- If destination has ONLY a generic Name/Full Name column (no separate First + Last), and source has separate first/last fields (mapped OR unmapped), you SHOULD combine them into that generic column and include ALL participating source keys in enhanced_mapping with combine flags.",
            "- If destination has separate First Name + Last Name columns and source is already split, map 1:1 — do NOT combine.",
            "- If source has one compound name field and destination has First + Last, consider split_2.",
            "- Use exact source field keys from the full source list when adding enhanced_mapping entries for fields omitted from Current Mapping.",
        ]
    )
    return "\n".join(lines)


def source_fields_from_flat_data(flat_data: Dict[str, Any]) -> List[str]:
    """Authoritative source column list for mapping UI (extraction preview keys)."""
    if not isinstance(flat_data, dict):
        return []
    skip = frozenset(
        {
            "filter_rules",
            "deduplication_rules",
            "transformation_rules",
            "data_cleanup_rules",
            "ordering_rules",
            "source_schema",
            "mapping_context_summary",
            "document_type",
            "document_description",
            "object_name",
        }
    )
    return sorted(k for k in flat_data.keys() if k and k not in skip)


def sample_row_for_mapping_transform(
    flat_data: Any,
    sample_data: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """First row used for programmatic split/combine heuristics."""
    if isinstance(flat_data, dict) and flat_data:
        return flat_data
    if sample_data and isinstance(sample_data[0], dict):
        return sample_data[0]
    return flat_data if isinstance(flat_data, dict) else {}


def _base_destination_name_shape(
    destination_name_shape: Dict[str, Any],
    base_container: str,
) -> Optional[Dict[str, Any]]:
    if not base_container or not isinstance(destination_name_shape, dict):
        return None
    base = str(base_container).strip()
    for key, val in destination_name_shape.items():
        if str(key).strip().lower() == base.lower() and isinstance(val, dict):
            return val
    direct = destination_name_shape.get(base)
    return direct if isinstance(direct, dict) else None


def extraction_schema_looks_destination_shaped(flat_data: Dict[str, Any]) -> bool:
    """
    True when extract output uses generic headers (e.g. name + email) without first/last sources.

    Informational only — mapping transform decisions use destination field shape, not this flag.
    """
    if not flat_data:
        return False
    has_split_name_sources = any(
        is_likely_first_name_source(k) or is_likely_last_name_source(k) for k in flat_data
    )
    if has_split_name_sources:
        return False
    keys_lower = {str(k).strip().lower() for k in flat_data}
    generic = keys_lower & {"name", "email", "phone", "mobile", "contact number", "mobile number"}
    return bool(generic) and not has_split_name_sources


def _primary_generic_name_column_for_base(
    dest_name_shape: Dict[str, Any],
    base_container: str,
) -> Optional[Tuple[str, str]]:
    """Return (container, generic_name_column) scoped to the base object/sheet/table only."""
    if not isinstance(dest_name_shape, dict) or not base_container:
        return None
    base = str(base_container).strip()
    shape = None
    for key, val in dest_name_shape.items():
        if str(key).strip().lower() == base.lower():
            shape = val
            break
    if not isinstance(shape, dict):
        return None
    if shape.get("has_separate_first_last"):
        return None
    generics = shape.get("generic_name_columns") or []
    if generics:
        return base, str(generics[0])
    return None


def _build_combine_result(
    *,
    source_fields: List[str],
    mapping: Dict[str, Any],
    target_obj: str,
    target_field: str,
    destination_type: str,
    dest_key: str,
    obj_key: str,
) -> Dict[str, Any]:
    enhanced: Dict[str, Any] = {}
    combine_group = f"{target_obj}.{target_field}".lower().replace(" ", "_")
    for idx, src in enumerate(source_fields):
        base = dict(mapping[src]) if isinstance(mapping.get(src), dict) else {}
        base[obj_key] = target_obj
        base[dest_key] = target_field
        base["is_combined_field"] = True
        base["combine_group"] = combine_group
        base["combine_order"] = idx
        base.pop("is_split_field", None)
        enhanced[src] = base

    rule: Dict[str, Any] = {
        "source_fields": source_fields,
        "action": "combine",
        "separator": " ",
        "output_field": target_field,
    }
    if destination_type == "salesforce":
        rule["output_object"] = target_obj
    elif destination_type == "sql":
        rule["output_table"] = target_obj
    elif destination_type in ("google_sheets", "gsheets"):
        rule["output_sheet"] = target_obj

    return {
        "transformation_rules": {"fields": [rule]},
        "enhanced_mapping": enhanced,
        "skip_llm": True,
    }


def try_programmatic_name_combine_for_mapping(
    *,
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    base_container: Optional[str] = None,
    audit: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build combine rules when base has a single generic Name column and first/last need combining.
    Never overwrites a healthy base split (Contact.FirstName + Contact.LastName).
    """
    from utils.mapping_audit import MappingAuditResult, audit_mapping_structure
    from utils.mapping_structure import resolve_base_container

    classified = classify_source_name_fields(flat_data or {})
    first_keys = classified.get("first_name_sources") or []
    last_keys = classified.get("last_name_sources") or []
    if not first_keys or not last_keys:
        return None

    base = resolve_base_container(
        base_container=base_container,
        mapping=mapping,
        destination_type=destination_type,
    )
    if not base:
        return None

    if audit is None:
        audit = audit_mapping_structure(
            mapping=mapping or {},
            flat_data=flat_data or {},
            destination_type=destination_type,
            base_container=base,
            destination_name_shape=destination_name_shape,
        )
    elif isinstance(audit, MappingAuditResult):
        if audit.has_code("BASE_NAME_SPLIT_OK"):
            return None
        if not audit.allow_programmatic_transform:
            return None
    else:
        if not getattr(audit, "allow_programmatic_transform", True):
            return None

    primary = _primary_generic_name_column_for_base(destination_name_shape or {}, base)
    if not primary:
        return None
    target_obj, target_field = primary

    fn = first_keys[0]
    ln = last_keys[0]
    source_fields = [fn, ln]

    if destination_type in ("google_sheets", "gsheets"):
        dest_key, obj_key = "column", "sheet"
    elif destination_type == "sql":
        dest_key, obj_key = "column", "table"
    else:
        dest_key, obj_key = "field", "object"

    def _base_dest_tuple(src: str) -> Optional[Tuple[str, str]]:
        info = mapping.get(src)
        if not isinstance(info, dict) or info.get("lookup_of"):
            return None
        o = (info.get(obj_key) or "").strip()
        f = (info.get(dest_key) or "").strip()
        if not o or not f:
            return None
        if o.lower() != target_obj.lower():
            return None
        return (o, f)

    fn_dest = _base_dest_tuple(fn)
    ln_dest = _base_dest_tuple(ln)

    if (
        fn_dest
        and ln_dest
        and fn_dest == ln_dest
        and fn_dest == (target_obj, target_field)
    ):
        return _build_combine_result(
            source_fields=source_fields,
            mapping=mapping,
            target_obj=target_obj,
            target_field=target_field,
            destination_type=destination_type,
            dest_key=dest_key,
            obj_key=obj_key,
        )

    if fn_dest and ln_dest and fn_dest != ln_dest:
        return None

    combine_hint = False
    if isinstance(audit, MappingAuditResult):
        combine_hint = any(h.action == "combine" for h in audit.hints)
    if not fn_dest and not ln_dest and combine_hint:
        return _build_combine_result(
            source_fields=source_fields,
            mapping=mapping,
            target_obj=target_obj,
            target_field=target_field,
            destination_type=destination_type,
            dest_key=dest_key,
            obj_key=obj_key,
        )

    return None


def _build_split_result(
    *,
    source_key: str,
    mapping_entry: Dict[str, Any],
    first_dest_field: str,
    last_dest_field: str,
    destination_type: str,
    dest_key: str,
    obj_key: str,
) -> Dict[str, Any]:
    base = {k: v for k, v in mapping_entry.items() if k not in (dest_key, "is_split_field")}
    dup0 = bool(mapping_entry.get("is_duplicate_check", False))
    lookup_of = (mapping_entry.get("lookup_of") or "").strip()
    target_obj = (mapping_entry.get(obj_key) or "").strip()
    mapped_dest_field = (mapping_entry.get(dest_key) or "").strip()

    def _slot_is_required(dest_field: str) -> bool:
        if mapped_dest_field == dest_field and mapping_entry.get("is_required"):
            return True
        return False

    first_entry = {
        **base,
        dest_key: first_dest_field,
        "is_split_field": True,
        "is_duplicate_check": dup0,
        "is_required": _slot_is_required(first_dest_field),
    }
    second_entry = {
        **base,
        dest_key: last_dest_field,
        "is_split_field": True,
        "is_duplicate_check": True,
        "is_required": _slot_is_required(last_dest_field),
    }
    if lookup_of:
        first_entry["lookup_of"] = lookup_of
        second_entry["lookup_of"] = lookup_of

    rule: Dict[str, Any] = {
        "source_field": source_key,
        "action": "split_2",
        "split_char": " ",
        "output_fields": [first_dest_field, last_dest_field],
    }
    if destination_type == "salesforce":
        rule["output_object"] = target_obj
    elif destination_type == "sql":
        rule["output_table"] = target_obj
    elif destination_type in ("google_sheets", "gsheets"):
        rule["output_sheet"] = target_obj

    return {
        "transformation_rules": {"fields": [rule]},
        "enhanced_mapping": {source_key: [first_entry, second_entry]},
        "skip_llm": True,
    }


def try_programmatic_name_split_for_mapping(
    *,
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    base_container: Optional[str] = None,
    audit: Optional[Any] = None,
    sample_data: Optional[List[Any]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Split a compound person-name source into destination first + last columns when the draft
    mapping sends the full name to only one component field on the base object.
    """
    from utils.mapping_structure import adapter_keys, resolve_base_container

    if audit is not None:
        from utils.mapping_audit import MappingAuditResult

        if isinstance(audit, MappingAuditResult) and audit.has_code("BASE_NAME_SPLIT_OK"):
            return None

    base = resolve_base_container(
        base_container=base_container,
        mapping=mapping,
        destination_type=destination_type,
    )
    if not base:
        return None

    base_shape = _base_destination_name_shape(destination_name_shape or {}, base)
    if not base_shape or not base_shape.get("has_separate_first_last"):
        return None

    first_col = (base_shape.get("first_name_column") or "").strip()
    last_col = (base_shape.get("last_name_column") or "").strip()
    if not first_col or not last_col or first_col.lower() == last_col.lower():
        return None

    classified = classify_source_name_fields(flat_data or {})
    compound_keys: Set[str] = set(classified.get("compound_name_sources") or [])
    for key in (mapping or {}).keys():
        if is_likely_compound_name_source(str(key)):
            compound_keys.add(str(key))

    dest_key, obj_key = adapter_keys(destination_type)
    row = sample_row_for_mapping_transform(flat_data, sample_data)
    generic_single = frozenset(
        {
            "name",
            "fullname",
            "full_name",
            "displayname",
            "display_name",
            "personname",
            "person_name",
        }
    )
    fn_l = first_col.lower()
    ln_l = last_col.lower()

    for source_key in sorted(compound_keys):
        info = (mapping or {}).get(source_key)
        if isinstance(info, list):
            continue
        if not isinstance(info, dict):
            continue
        if info.get("lookup_of"):
            continue
        container = (info.get(obj_key) or "").strip()
        if not container or container.lower() != base.lower():
            continue

        cur_field = (info.get(dest_key) or "").strip()
        if not cur_field:
            continue
        c_low = cur_field.lower()
        if not (c_low == fn_l or c_low == ln_l or c_low in generic_single):
            continue

        raw_val = row.get(source_key)
        if raw_val is None:
            from utils.mapping_utils import resolve_extracted_field_key

            alt = resolve_extracted_field_key(source_key, list(row.keys()))
            raw_val = row.get(alt)
        if not isinstance(raw_val, str) or len(raw_val.split()) < 2:
            continue

        return _build_split_result(
            source_key=source_key,
            mapping_entry=info,
            first_dest_field=first_col,
            last_dest_field=last_col,
            destination_type=destination_type,
            dest_key=dest_key,
            obj_key=obj_key,
        )

    return None


def log_mapping_transform_resolution(resolution: str, destination_type: str) -> None:
    print(
        f"🔧 [MAP_TRANSFORM] resolution={resolution} "
        f"destination_type={destination_type or 'unknown'}"
    )


def _transform_rules_list(rules: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(rules, dict):
        return []
    fields = rules.get("fields")
    return list(fields) if isinstance(fields, list) else []


def _merge_programmatic_transform_result(
    *,
    transformation_rules: Dict[str, Any],
    enhanced_mapping: Dict[str, Any],
    prog: Dict[str, Any],
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    rules_out = dict(transformation_rules or {"fields": []})
    fields = _transform_rules_list(rules_out)
    for rule in _transform_rules_list(prog.get("transformation_rules")):
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action") or "")
        if action.startswith("split"):
            src = rule.get("source_field")
            if src and any(
                isinstance(r, dict)
                and r.get("action", "").startswith("split")
                and r.get("source_field") == src
                for r in fields
            ):
                continue
        elif action == "combine":
            srcs = set(rule.get("source_fields") or [])
            if srcs and any(
                isinstance(r, dict)
                and r.get("action") == "combine"
                and set(r.get("source_fields") or []) == srcs
                for r in fields
            ):
                continue
        fields.append(rule)
    rules_out["fields"] = fields
    em_out = dict(enhanced_mapping or {})
    for key, value in (prog.get("enhanced_mapping") or {}).items():
        em_out[key] = value
    return rules_out, em_out


def try_programmatic_combine_from_slot_collisions(
    *,
    mapping: Dict[str, Any],
    destination_type: str,
    transformation_rules: Dict[str, Any],
    base_container: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """When call 1 mapped multiple sources to the same base slot, emit combine rules."""
    from utils.mapping_structure import (
        adapter_keys,
        base_entries,
        dest_slot_for_entry,
        normalize_mapping_entries,
        resolve_base_container,
    )

    base = resolve_base_container(
        base_container=base_container,
        mapping=mapping,
        destination_type=destination_type,
    )
    if not base:
        return None

    dest_key, obj_key = adapter_keys(destination_type)
    entries = normalize_mapping_entries(
        mapping or {},
        destination_type=destination_type,
        base_container=base,
    )
    slot_sources: Dict[tuple, List[str]] = {}
    for entry in base_entries(entries):
        if entry.lookup_of:
            continue
        slot = dest_slot_for_entry(entry)
        if not slot or slot.container.lower() != base.lower():
            continue
        slot_sources.setdefault(slot.key(), [])
        if entry.source_key not in slot_sources[slot.key()]:
            slot_sources[slot.key()].append(entry.source_key)

    existing = _transform_rules_list(transformation_rules)
    for _slot_key, sources in slot_sources.items():
        if len(sources) < 2:
            continue
        if any(
            isinstance(r, dict)
            and r.get("action") == "combine"
            and set(r.get("source_fields") or []) == set(sources)
            for r in existing
        ):
            continue
        first_info = mapping.get(sources[0])
        if not isinstance(first_info, dict):
            continue
        target_obj = (first_info.get(obj_key) or "").strip()
        target_field = (first_info.get(dest_key) or "").strip()
        if not target_obj or not target_field:
            continue
        return _build_combine_result(
            source_fields=sources,
            mapping=mapping,
            target_obj=target_obj,
            target_field=target_field,
            destination_type=destination_type,
            dest_key=dest_key,
            obj_key=obj_key,
        )
    return None


def apply_post_llm_transform_reconciliation(
    *,
    mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    transformation_rules: Dict[str, Any],
    enhanced_mapping: Dict[str, Any],
    sample_data: Optional[List[Any]] = None,
    base_container: Optional[str] = None,
    base_objects: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Single post-call-1 transform reconciliation (split / combine) for all connectors.
    Replaces per-handler split/combine fallback blocks.
    """
    from utils.mapping_audit import MappingAuditResult, audit_mapping_structure

    dest_type = (destination_type or "salesforce").lower()
    if dest_type == "gsheets":
        dest_type = "google_sheets"

    rules = dict(transformation_rules or {"fields": []})
    em = dict(enhanced_mapping or {})
    resolutions: List[str] = []

    audit = audit_mapping_structure(
        mapping=mapping or {},
        flat_data=flat_data or {},
        destination_type=dest_type,
        base_container=base_container,
        base_objects=base_objects,
        destination_name_shape=destination_name_shape,
    )

    split_prog = try_programmatic_name_split_for_mapping(
        flat_data=flat_data or {},
        mapping=mapping or {},
        destination_name_shape=destination_name_shape or {},
        destination_type=dest_type,
        base_container=base_container or getattr(audit, "base_container", None),
        audit=audit,
        sample_data=sample_data,
    )
    if split_prog:
        rules, em = _merge_programmatic_transform_result(
            transformation_rules=rules,
            enhanced_mapping=em,
            prog=split_prog,
        )
        resolutions.append("programmatic_split")

    combine_prog = try_programmatic_name_combine_for_mapping(
        flat_data=flat_data or {},
        mapping=mapping or {},
        destination_name_shape=destination_name_shape or {},
        destination_type=dest_type,
        base_container=base_container or getattr(audit, "base_container", None),
        audit=audit,
    )
    if combine_prog:
        rules, em = _merge_programmatic_transform_result(
            transformation_rules=rules,
            enhanced_mapping=em,
            prog=combine_prog,
        )
        resolutions.append("programmatic_combine")

    collision_prog = try_programmatic_combine_from_slot_collisions(
        mapping=mapping or {},
        destination_type=dest_type,
        transformation_rules=rules,
        base_container=base_container or getattr(audit, "base_container", None),
    )
    if collision_prog:
        rules, em = _merge_programmatic_transform_result(
            transformation_rules=rules,
            enhanced_mapping=em,
            prog=collision_prog,
        )
        if "programmatic_combine" not in resolutions:
            resolutions.append("programmatic_combine_collision")

    resolution = "+".join(resolutions) if resolutions else "llm_only"
    log_mapping_transform_resolution(resolution, dest_type)
    return {
        "transformation_rules": rules,
        "enhanced_mapping": em,
        "transform_resolution": resolution,
    }


def mapping_destination_transform_still_needed(
    audit: Any,
    *,
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    base_container: Optional[str] = None,
) -> bool:
    """True when destination shape vs draft mapping still needs split/combine reconciliation."""
    from utils.mapping_audit import MappingAuditResult

    if isinstance(audit, MappingAuditResult):
        if audit.has_code("BASE_NAME_SPLIT_OK"):
            return False
        if audit.has_code("BASE_NAME_SPLIT_NEEDED"):
            if try_programmatic_name_split_for_mapping(
                flat_data=flat_data,
                mapping=mapping,
                destination_name_shape=destination_name_shape,
                destination_type=destination_type,
                base_container=base_container,
                audit=audit,
            ):
                return False
            return True
        if any(h.action in ("split", "combine") for h in audit.hints):
            return True

    return False


def should_skip_mapping_transform_llm(
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    base_container: Optional[str] = None,
    base_objects: Optional[List[str]] = None,
) -> bool:
    """Skip transform LLM when schema is dest-shaped, audit is ok, or programmatic combine applies."""
    from utils.mapping_audit import resolve_post_map_transforms

    resolved = resolve_post_map_transforms(
        flat_data=flat_data or {},
        mapping=mapping or {},
        destination_name_shape=destination_name_shape or {},
        destination_type=destination_type,
        base_container=base_container,
        base_objects=base_objects,
    )
    if resolved.get("skip_transform_llm"):
        return True
    audit = resolved.get("mapping_audit") or {}
    if audit.get("status") == "ok" and audit.get("allow_programmatic_transform") is False:
        return True
    return bool(
        resolved.get("enhanced_mapping")
        and resolved.get("transformation_rules", {}).get("fields")
    )
