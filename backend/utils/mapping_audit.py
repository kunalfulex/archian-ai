"""
Deterministic structural audit of LLM field mappings (base object first, then lookups).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from utils.mapping_structure import (
    NormalizedEntry,
    base_entries,
    dest_slot_for_entry,
    entries_for_source,
    normalize_mapping_entries,
    resolve_base_container,
)
from utils.mapping_transform_context import (
    classify_source_name_fields,
    is_likely_compound_name_source,
    is_likely_first_name_source,
    is_likely_last_name_source,
)
from utils.mapping_structure import adapter_keys
from utils.mapping_utils import resolve_extracted_field_key


def _extract_data_keys(flat_data: Dict[str, Any]) -> List[str]:
    if not isinstance(flat_data, dict):
        return []
    return [
        k
        for k in flat_data.keys()
        if k and not str(k).startswith("_")
        and str(k).lower() not in ("attachments", "_attachments")
    ]


def _resolved_source_key(source_key: str, data_keys: List[str]) -> str:
    return resolve_extracted_field_key(source_key, data_keys)


def _entries_for_resolved_source(
    entries: List[NormalizedEntry], source_key: str, data_keys: List[str]
) -> List[NormalizedEntry]:
    return [
        e
        for e in entries
        if _resolved_source_key(e.source_key, data_keys) == source_key
    ]


def _mapping_has_resolved_source(
    mapping: Dict[str, Any], source_key: str, data_keys: List[str]
) -> bool:
    for k in (mapping or {}):
        if _resolved_source_key(k, data_keys) == source_key:
            return True
    return False


def _is_account_or_lookup_source_key(key: str, mapping_entry: Any = None) -> bool:
    """Sources that must not be name-split (AccountName, company, lookup rows)."""
    n = (key or "").lower().replace("_", "").replace(" ", "")
    if n in (
        "company",
        "accountname",
        "accountphone",
        "accountid",
        "organization",
        "employer",
    ):
        return True
    if "account" in n and any(t in n for t in ("name", "phone", "id")):
        return True
    if isinstance(mapping_entry, dict) and mapping_entry.get("lookup_of"):
        return True
    if isinstance(mapping_entry, list):
        return any(
            isinstance(item, dict) and item.get("lookup_of") for item in mapping_entry
        )
    return False


def resolve_mapping_key_for_enhanced(
    enhanced_key: str,
    mapping: Dict[str, Any],
    data_keys: List[str],
) -> Optional[str]:
    """Find the mapping dict key that corresponds to an enhanced_mapping key."""
    if enhanced_key in mapping:
        return enhanced_key
    target = _resolved_source_key(enhanced_key, data_keys)
    for mk in mapping:
        if _resolved_source_key(mk, data_keys) == target:
            return mk
    return None


def sanitize_enhanced_mapping(
    mapping: Dict[str, Any],
    enhanced_mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Drop transform LLM entries that would corrupt lookup/account mappings or apply
    invalid single-item 'splits'.
    """
    if not enhanced_mapping:
        return {}
    data_keys = _extract_data_keys(flat_data)
    out: Dict[str, Any] = {}
    for key, value in enhanced_mapping.items():
        map_key = resolve_mapping_key_for_enhanced(key, mapping, data_keys)
        original = mapping.get(map_key) if map_key else mapping.get(key)
        if _is_account_or_lookup_source_key(key, original):
            continue
        if map_key and _is_account_or_lookup_source_key(map_key, original):
            continue
        if isinstance(value, list):
            if len(value) < 2:
                continue
            if isinstance(original, dict) and original.get("lookup_of"):
                continue
            dest_fields = {
                (item.get("field") or "").lower()
                for item in value
                if isinstance(item, dict)
            }
            if "accountid" in dest_fields and (
                (original or {}).get("object") == "Account"
                or any(
                    isinstance(item, dict) and item.get("object") == "Account"
                    for item in value
                )
            ):
                continue
        out[key] = value
    return out


def apply_enhanced_mapping_safely(
    mapping: Dict[str, Any],
    enhanced_mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
) -> tuple[Dict[str, Any], set]:
    """
    Apply sanitized enhanced_mapping entries to mapping.
    Returns updated mapping and set of skipped source keys.
    """
    skipped: set = set()
    if not enhanced_mapping:
        return mapping, skipped
    data_keys = _extract_data_keys(flat_data)
    cleaned = sanitize_enhanced_mapping(mapping, enhanced_mapping, flat_data)
    for key, value in cleaned.items():
        map_key = resolve_mapping_key_for_enhanced(key, mapping, data_keys) or key
        original_mapping_entry = mapping.get(map_key)
        original_lookup_of = (
            original_mapping_entry.get("lookup_of")
            if isinstance(original_mapping_entry, dict)
            else None
        )
        if isinstance(value, list):
            if len(value) < 2:
                skipped.add(map_key)
                continue
            if _is_account_or_lookup_source_key(map_key, original_mapping_entry):
                skipped.add(map_key)
                continue
            for item in value:
                if isinstance(item, dict):
                    item["is_split_field"] = True
                    item_lookup_of = item.get("lookup_of")
                    if original_lookup_of and not item_lookup_of:
                        item["lookup_of"] = original_lookup_of
                    elif not original_lookup_of and item_lookup_of:
                        item.pop("lookup_of", None)
            mapping[map_key] = value
        elif isinstance(value, dict):
            if value.get("is_combined_field"):
                combined_lookup_of = value.get("lookup_of")
                if original_lookup_of and not combined_lookup_of:
                    value["lookup_of"] = original_lookup_of
                mapping[map_key] = value
            else:
                regular_lookup_of = value.get("lookup_of")
                if original_lookup_of and not regular_lookup_of:
                    value["lookup_of"] = original_lookup_of
                mapping[map_key] = value
    return mapping, skipped


def compute_mapping_score(audit: MappingAuditResult) -> float:
    if audit.status == "error":
        return 0.0
    score = 1.0
    for issue in audit.issues:
        if issue.severity == "warn":
            score -= 0.12
        elif issue.severity == "error":
            score -= 0.35
    return round(max(0.0, min(1.0, score)), 2)


def migration_allowed_by_audit(audit: Optional[Dict[str, Any]]) -> bool:
    if not audit or not isinstance(audit, dict):
        return True
    return bool(audit.get("allow_auto_migration", True))


@dataclass
class MappingIssue:
    code: str
    severity: str  # info | warn | error
    message: str
    source_keys: List[str] = field(default_factory=list)


@dataclass
class MappingHint:
    action: str  # combine | split | none
    base_container: str
    source_fields: List[str] = field(default_factory=list)
    dest_field: str = ""


@dataclass
class MappingAuditResult:
    status: str  # ok | warn | error
    issues: List[MappingIssue] = field(default_factory=list)
    hints: List[MappingHint] = field(default_factory=list)
    allow_programmatic_transform: bool = True
    allow_auto_migration: bool = True
    base_container: str = ""

    def has_code(self, code: str) -> bool:
        return any(i.code == code for i in self.issues)


def _containers_match(a: str, b: str) -> bool:
    return (a or "").strip().lower() == (b or "").strip().lower()


def audit_mapping_structure(
    mapping: Dict[str, Any],
    flat_data: Dict[str, Any],
    *,
    destination_type: str,
    base_container: Optional[str] = None,
    base_objects: Optional[List[str]] = None,
    document_type: Optional[str] = None,
    destination_name_shape: Optional[Dict[str, Any]] = None,
) -> MappingAuditResult:
    base = resolve_base_container(
        base_container=base_container,
        base_objects=base_objects,
        document_type=document_type,
        mapping=mapping,
        destination_type=destination_type,
    )
    result = MappingAuditResult(status="ok", base_container=base)
    if not base or not mapping:
        return result

    data_keys = _extract_data_keys(flat_data or {})
    entries = normalize_mapping_entries(
        mapping, destination_type=destination_type, base_container=base
    )
    base_only = base_entries(entries)
    classified = classify_source_name_fields(flat_data or {})
    first_keys = classified.get("first_name_sources") or []
    last_keys = classified.get("last_name_sources") or []
    fn = first_keys[0] if first_keys else None
    ln = last_keys[0] if last_keys else None

    for src_key in list((mapping or {}).keys()):
        resolved = _resolved_source_key(src_key, data_keys)
        if data_keys and resolved not in data_keys and src_key not in data_keys:
            result.issues.append(
                MappingIssue(
                    code="MAPPING_KEY_NOT_IN_EXTRACT",
                    severity="warn",
                    message=(
                        f"Mapping key '{src_key}' does not match any extracted column "
                        f"(resolved: '{resolved}')."
                    ),
                    source_keys=[src_key],
                )
            )
            if result.status == "ok":
                result.status = "warn"

    # --- Split present (1 source → multiple base dest slots) ---
    for src in {e.source_key for e in base_only}:
        src_entries = [e for e in base_only if e.source_key == src]
        if len(src_entries) >= 2 and any(e.is_list_mapping for e in entries_for_source(entries, src)):
            result.issues.append(
                MappingIssue(
                    code="BASE_SPLIT_PRESENT",
                    severity="info",
                    message=f"Source '{src}' maps to multiple base fields (split).",
                    source_keys=[src],
                )
            )

    # --- Combine present (N sources → same base slot) ---
    slot_sources: Dict[tuple, List[str]] = {}
    for e in base_only:
        slot = dest_slot_for_entry(e)
        if not slot:
            continue
        key = slot.key()
        slot_sources.setdefault(key, [])
        if e.source_key not in slot_sources[key]:
            slot_sources[key].append(e.source_key)

    for slot_key, sources in slot_sources.items():
        if len(sources) >= 2:
            result.issues.append(
                MappingIssue(
                    code="BASE_COMBINE_PRESENT",
                    severity="info",
                    message=f"Sources {sources} map to base slot {slot_key}.",
                    source_keys=sources,
                )
            )

    # --- Healthy base split for first + last ---
    healthy_split = False
    if fn and ln:
        fn_entries = _entries_for_resolved_source(base_only, fn, data_keys)
        ln_entries = _entries_for_resolved_source(base_only, ln, data_keys)
        if fn_entries and ln_entries:
            fn_slot = dest_slot_for_entry(fn_entries[0])
            ln_slot = dest_slot_for_entry(ln_entries[0])
            if (
                fn_slot
                and ln_slot
                and _containers_match(fn_slot.container, base)
                and _containers_match(ln_slot.container, base)
                and fn_slot.key() != ln_slot.key()
            ):
                healthy_split = True
                result.issues.append(
                    MappingIssue(
                        code="BASE_NAME_SPLIT_OK",
                        severity="info",
                        message=(
                            f"'{fn}' and '{ln}' map to distinct base fields on {base} "
                            f"({fn_slot.field} / {ln_slot.field})."
                        ),
                        source_keys=[fn, ln],
                    )
                )

    if healthy_split:
        result.allow_programmatic_transform = False

    # --- Lookup hijack: person-name sources on lookup Name while base has split ---
    if healthy_split and fn and ln:
        for src in (fn, ln):
            for e in entries_for_source(entries, src):
                if e.scope == "lookup" and (e.dest_field or "").lower() == "name":
                    result.issues.append(
                        MappingIssue(
                            code="LOOKUP_HIJACK_RISK",
                            severity="error",
                            message=f"'{src}' maps to lookup Name; base split mapping should be preserved.",
                            source_keys=[src],
                        )
                    )
                    result.status = "error"
                    result.allow_auto_migration = False

    # --- Person names wrongly on non-base container ---
    if fn and ln and not healthy_split:
        for src in (fn, ln):
            for e in entries_for_source(entries, src):
                if e.scope == "base" and not _containers_match(e.dest_container, base):
                    result.issues.append(
                        MappingIssue(
                            code="COMBINE_WOULD_OVERRIDE_LLM",
                            severity="error",
                            message=(
                                f"'{src}' maps to {e.dest_container}.{e.dest_field}, "
                                f"not base object {base}."
                            ),
                            source_keys=[src],
                        )
                    )
                    result.status = "error"
                    result.allow_auto_migration = False

    # --- Compound source mapped to only one of first/last on base (needs split) ---
    dest_shape = destination_name_shape or {}
    base_shape = None
    if isinstance(dest_shape, dict):
        for key, val in dest_shape.items():
            if _containers_match(str(key), base) and isinstance(val, dict):
                base_shape = val
                break
        if base_shape is None and isinstance(dest_shape.get(base), dict):
            base_shape = dest_shape.get(base)

    if isinstance(base_shape, dict) and base_shape.get("has_separate_first_last"):
        fn_col = (base_shape.get("first_name_column") or "").strip()
        ln_col = (base_shape.get("last_name_column") or "").strip()
        field_key, container_key = adapter_keys(destination_type)
        compound_keys = set(classified.get("compound_name_sources") or [])
        for src_key in (mapping or {}).keys():
            if is_likely_compound_name_source(str(src_key)):
                compound_keys.add(str(src_key))

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
        fn_l = fn_col.lower() if fn_col else ""
        ln_l = ln_col.lower() if ln_col else ""

        for src_key in compound_keys:
            if _is_account_or_lookup_source_key(src_key, (mapping or {}).get(src_key)):
                continue
            info = (mapping or {}).get(src_key)
            if isinstance(info, list):
                continue
            if not isinstance(info, dict) or info.get("lookup_of"):
                continue
            if not _containers_match((info.get(container_key) or "").strip(), base):
                continue
            cur_field = (info.get(field_key) or "").strip()
            if not cur_field or not fn_col or not ln_col:
                continue
            c_low = cur_field.lower()
            if not (c_low == fn_l or c_low == ln_l or c_low in generic_single):
                continue
            result.issues.append(
                MappingIssue(
                    code="BASE_NAME_SPLIT_NEEDED",
                    severity="warn",
                    message=(
                        f"Compound source '{src_key}' maps to a single name component "
                        f"({base}.{cur_field}) but destination has separate "
                        f"{fn_col} and {ln_col}."
                    ),
                    source_keys=[src_key],
                )
            )
            result.hints.append(
                MappingHint(
                    action="split",
                    base_container=base,
                    source_fields=[src_key],
                    dest_field=fn_col,
                )
            )
            if result.status == "ok":
                result.status = "warn"

    # --- Suggest base combine (destination has single Name on base, sources need combine) ---
    if base_shape is None and isinstance(dest_shape, dict):
        base_shape = dest_shape.get(base) if isinstance(dest_shape.get(base), dict) else None
    if isinstance(base_shape, dict) and fn and ln:
        generics = base_shape.get("generic_name_columns") or []
        name_only = bool(generics) and not base_shape.get("has_separate_first_last")
        if name_only:
            fn_mapped = _mapping_has_resolved_source(mapping or {}, fn, data_keys)
            ln_mapped = _mapping_has_resolved_source(mapping or {}, ln, data_keys)
            need_combine = False
            if not fn_mapped and not ln_mapped:
                need_combine = True
            elif fn_mapped and ln_mapped:
                fn_e = entries_for_source(entries, fn)
                ln_e = entries_for_source(entries, ln)
                if fn_e and ln_e:
                    fs = dest_slot_for_entry(fn_e[0])
                    ls = dest_slot_for_entry(ln_e[0])
                    if (
                        fs
                        and ls
                        and fs.key() == ls.key()
                        and _containers_match(fs.container, base)
                    ):
                        need_combine = True
            if need_combine and not healthy_split:
                generics = base_shape.get("generic_name_columns") or ["Name"]
                result.hints.append(
                    MappingHint(
                        action="combine",
                        base_container=base,
                        source_fields=[fn, ln],
                        dest_field=str(generics[0]),
                    )
                )
                if not result.has_code("BASE_COMBINE_PRESENT"):
                    result.allow_programmatic_transform = True

    if result.status == "error":
        result.allow_programmatic_transform = False
    elif result.status == "ok" and result.has_code("BASE_NAME_SPLIT_OK"):
        result.allow_auto_migration = True

    return result


def audit_result_to_dict(audit: MappingAuditResult) -> Dict[str, Any]:
    score = compute_mapping_score(audit)
    allow_auto = audit.allow_auto_migration
    if audit.status in ("error", "warn") and score < 0.75:
        allow_auto = False
    return {
        "status": audit.status,
        "mapping_score": score,
        "base_container": audit.base_container,
        "allow_programmatic_transform": audit.allow_programmatic_transform,
        "allow_auto_migration": allow_auto,
        "issues": [
            {
                "code": i.code,
                "severity": i.severity,
                "message": i.message,
                "source_keys": i.source_keys,
            }
            for i in audit.issues
        ],
        "hints": [
            {
                "action": h.action,
                "base_container": h.base_container,
                "source_fields": h.source_fields,
                "dest_field": h.dest_field,
            }
            for h in audit.hints
        ],
    }


def resolve_post_map_transforms(
    *,
    flat_data: Dict[str, Any],
    mapping: Dict[str, Any],
    destination_name_shape: Dict[str, Any],
    destination_type: str,
    base_container: Optional[str] = None,
    base_objects: Optional[List[str]] = None,
    document_type: Optional[str] = None,
    sample_data: Optional[List[Any]] = None,
) -> Dict[str, Any]:
    """
    Destination-first transform reconciliation after call-1 field mapping.

    Applies programmatic combine/split when destination columns require it, then decides
    whether the transform LLM (call 2) is still needed. Source shape after data ops does not
    skip this step.
    """
    from utils.mapping_transform_context import (
        log_mapping_transform_resolution,
        mapping_destination_transform_still_needed,
        try_programmatic_name_combine_for_mapping,
        try_programmatic_name_split_for_mapping,
    )

    audit = audit_mapping_structure(
        mapping=mapping or {},
        flat_data=flat_data or {},
        destination_type=destination_type,
        base_container=base_container,
        base_objects=base_objects,
        document_type=document_type,
        destination_name_shape=destination_name_shape,
    )
    audit_dict = audit_result_to_dict(audit)
    empty = {
        "transformation_rules": {"fields": []},
        "enhanced_mapping": {},
        "mapping_audit": audit_dict,
        "skip_transform_llm": False,
    }

    if audit.has_code("BASE_NAME_SPLIT_OK"):
        out = dict(empty)
        out["skip_transform_llm"] = True
        log_mapping_transform_resolution("none_aligned", destination_type)
        return out

    if audit.allow_programmatic_transform:
        prog_combine = try_programmatic_name_combine_for_mapping(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=destination_name_shape or {},
            destination_type=destination_type,
            base_container=audit.base_container,
            audit=audit,
        )
        if prog_combine and prog_combine.get("skip_llm"):
            log_mapping_transform_resolution("programmatic_combine", destination_type)
            return {
                "transformation_rules": prog_combine.get("transformation_rules") or {"fields": []},
                "enhanced_mapping": prog_combine.get("enhanced_mapping") or {},
                "mapping_audit": audit_dict,
                "skip_transform_llm": True,
            }

        prog_split = try_programmatic_name_split_for_mapping(
            flat_data=flat_data or {},
            mapping=mapping or {},
            destination_name_shape=destination_name_shape or {},
            destination_type=destination_type,
            base_container=audit.base_container,
            audit=audit,
            sample_data=sample_data,
        )
        if prog_split and prog_split.get("skip_llm"):
            log_mapping_transform_resolution("programmatic_split", destination_type)
            return {
                "transformation_rules": prog_split.get("transformation_rules") or {"fields": []},
                "enhanced_mapping": prog_split.get("enhanced_mapping") or {},
                "mapping_audit": audit_dict,
                "skip_transform_llm": True,
            }

    out = dict(empty)
    out["skip_transform_llm"] = mapping_destination_transform_still_needed(
        audit,
        flat_data=flat_data or {},
        mapping=mapping or {},
        destination_name_shape=destination_name_shape or {},
        destination_type=destination_type,
        base_container=audit.base_container,
    )
    if out["skip_transform_llm"]:
        log_mapping_transform_resolution("none_aligned", destination_type)
    else:
        log_mapping_transform_resolution("llm_required", destination_type)
    return out
