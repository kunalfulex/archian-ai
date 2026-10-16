"""
Compile migration_rules JSON for a bidirectional reverse-leg child flow.

v1: Invert mapping-stage split <-> combine (split_char <-> separator), rebind
filter / dedup / data_cleanup field paths using parent vs reverse field_mapping rows
(index-aligned). Falls back to parent rule copy when compiler is disabled or on error.

Compiler is on by default. Disable with: EDISON_REVERSE_MIGRATION_RULES_COMPILER=0
(or false/no/off).
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


ENV_ENABLE_REVERSE_RULES_COMPILER = "EDISON_REVERSE_MIGRATION_RULES_COMPILER"


def reverse_migration_rules_compiler_enabled() -> bool:
    v = os.environ.get(ENV_ENABLE_REVERSE_RULES_COMPILER, "").strip().lower()
    if not v:
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return True


@dataclass
class ReverseMigrationRulesCompileResult:
    """None migration_rules means caller should persist a copy of parent rules (legacy)."""

    migration_rules: Optional[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _parse_migration_rules_dict(migration_rules: Any) -> Optional[Dict[str, Any]]:
    if migration_rules is None:
        return None
    if isinstance(migration_rules, dict):
        return migration_rules
    if isinstance(migration_rules, str):
        try:
            import json

            parsed = json.loads(migration_rules)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _build_extract_key_rename_map(
    parent_mappings: List[Dict[str, Any]],
    reverse_mappings: List[Dict[str, Any]],
) -> Tuple[Dict[str, str], List[str]]:
    """
    Map forward extract / record keys (as used in filters) -> reverse extract column keys.

    Heuristic v1: for each index i, map parent extracted_field_name -> reverse extracted_field_name,
    and parent salesforce_field -> reverse extracted_field_name (covers filters on either side name).
    """
    warnings: List[str] = []
    out: Dict[str, str] = {}
    n = min(len(parent_mappings or []), len(reverse_mappings or []))
    if len(parent_mappings or []) != len(reverse_mappings or []):
        warnings.append(
            "reverse migration rules: parent vs reverse mapping row count differs; "
            "rebind map built from first N rows only."
        )
    for i in range(n):
        pr = parent_mappings[i] or {}
        rr = reverse_mappings[i] or {}
        pe = (pr.get("extracted_field_name") or "").strip()
        psf = (pr.get("salesforce_field") or "").strip()
        re = (rr.get("extracted_field_name") or "").strip()
        if not re:
            continue
        if pe:
            out.setdefault(pe.lower(), re)
        if psf:
            out.setdefault(psf.lower(), re)
    return out, warnings


def _rebind_field_key(name: str, rename_lower: Dict[str, str]) -> str:
    k = (name or "").strip()
    if not k:
        return k
    mapped = rename_lower.get(k.lower())
    return mapped if mapped else k


def _rebind_filter_rules(fr: Any, rename_lower: Dict[str, str], warnings: List[str]) -> Any:
    if not isinstance(fr, dict):
        return fr
    out = copy.deepcopy(fr)
    conds = out.get("conditions")
    if not isinstance(conds, list):
        return out
    for c in conds:
        if not isinstance(c, dict):
            continue
        fld = c.get("field")
        if isinstance(fld, str) and fld.strip():
            new_f = _rebind_field_key(fld, rename_lower)
            if new_f != fld:
                c["field"] = new_f
            elif fld.lower() not in rename_lower:
                warnings.append(
                    f"reverse migration rules: filter field '{fld}' had no mapping rebind target; left unchanged."
                )
    return out


def _rebind_deduplication_rules(d: Any, rename_lower: Dict[str, str]) -> Any:
    if not isinstance(d, dict):
        return d
    out = copy.deepcopy(d)
    fields = out.get("fields")
    if isinstance(fields, list):
        out["fields"] = [_rebind_field_key(str(f), rename_lower) if isinstance(f, str) else f for f in fields]
    return out


def _rebind_data_cleanup_rules(dc: Any, rename_lower: Dict[str, str]) -> Any:
    if not isinstance(dc, dict):
        return dc
    out: Dict[str, Any] = {}
    for k, v in dc.items():
        nk = _rebind_field_key(str(k), rename_lower)
        out[nk] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return out


def _invert_transformation_fields_list(
    fields: Any,
    warnings: List[str],
) -> List[Dict[str, Any]]:
    if not isinstance(fields, list):
        return []
    out: List[Dict[str, Any]] = []
    for rule in fields:
        if not isinstance(rule, dict):
            continue
        action = (rule.get("action") or "").strip().lower()
        new_rule: Optional[Dict[str, Any]] = None

        if action in ("combine", "merge"):
            srcs = rule.get("source_fields")
            if srcs is None:
                srcs = rule.get("source_field") or []
            if isinstance(srcs, str):
                srcs = [srcs]
            if not isinstance(srcs, list):
                srcs = []
            out_f = rule.get("output_field")
            sep = rule.get("separator", " ")
            if len(srcs) < 2 or not out_f:
                warnings.append(
                    "reverse migration rules: skipped non-invertible "
                    f"{action} rule (need 2+ source fields and output_field)."
                )
                continue
            n = len(srcs)
            act = f"split_{n}" if n <= 10 else "split"
            new_rule = {
                "source_field": str(out_f).strip(),
                "action": act,
                "split_char": sep if sep is not None else " ",
                "output_fields": [str(s).strip() for s in srcs if str(s).strip()],
            }
            if rule.get("output_object"):
                new_rule["output_object"] = rule.get("output_object")

        elif action.startswith("split") or action == "split_address":
            src = rule.get("source_field")
            outs = rule.get("output_fields") or []
            sc = rule.get("split_char", " ")
            if not src or not isinstance(outs, list) or len(outs) < 2:
                warnings.append("reverse migration rules: skipped non-invertible split rule (need source_field and 2+ output_fields).")
                continue
            outs_clean = [str(o).strip() for o in outs if str(o).strip()]
            new_rule = {
                "source_field": outs_clean,
                "action": "merge",
                "separator": sc if sc is not None else " ",
                "output_field": str(src).strip(),
            }
            if rule.get("output_object"):
                new_rule["output_object"] = rule.get("output_object")
        else:
            new_rule = copy.deepcopy(rule)

        if new_rule:
            out.append(new_rule)
    return out


def compile_reverse_child_migration_rules(
    parent_migration_rules: Any,
    parent_mappings: List[Dict[str, Any]],
    reverse_mappings: List[Dict[str, Any]],
) -> ReverseMigrationRulesCompileResult:
    """
    Build child migration_rules for the reverse leg.

    When disabled via EDISON_REVERSE_MIGRATION_RULES_COMPILER or parent has no rules dict,
    returns migration_rules=None (persist parent copy).
    """
    raw_env = os.environ.get(ENV_ENABLE_REVERSE_RULES_COMPILER, "")
    enabled = reverse_migration_rules_compiler_enabled()
    logger.debug(
        "%s raw=%r resolved_enabled=%s parent_mapping_rows=%d reverse_mapping_rows=%d "
        "(empty env means compiler ON by default)",
        ENV_ENABLE_REVERSE_RULES_COMPILER,
        raw_env,
        enabled,
        len(parent_mappings or []),
        len(reverse_mappings or []),
    )

    if not enabled:
        logger.debug(
            "reverse_migration_rules_compiler: skipping compile (env disables); child will use parent migration_rules copy"
        )
        return ReverseMigrationRulesCompileResult(migration_rules=None, stats={"enabled": False})

    parent = _parse_migration_rules_dict(parent_migration_rules)
    if not parent:
        logger.debug(
            "reverse_migration_rules_compiler: no parent rules dict; child will use parent migration_rules copy (stats reason=no_parent_rules)"
        )
        return ReverseMigrationRulesCompileResult(
            migration_rules=None,
            stats={"enabled": True, "reason": "no_parent_rules"},
        )

    warnings: List[str] = []
    errors: List[str] = []
    rename_lower, wmap = _build_extract_key_rename_map(parent_mappings, reverse_mappings)
    warnings.extend(wmap)

    # Preserve unknown keys; patch only sections the compiler understands.
    child: Dict[str, Any] = copy.deepcopy(parent)

    # Top-level mapping transforms (split <-> combine) — not extraction_rules.transformation_rules
    from utils.migration_rules_utils import (
        LEGACY_MAPPING_TRANSFORMATION_KEY,
        MAPPING_TRANSFORMATION_KEY,
        get_mapping_transformation,
    )

    tr = get_mapping_transformation(child)
    if isinstance(tr, dict) and isinstance(tr.get("fields"), list):
        child[MAPPING_TRANSFORMATION_KEY] = {
            **tr,
            "fields": _invert_transformation_fields_list(tr.get("fields"), warnings),
        }
        child.pop(LEGACY_MAPPING_TRANSFORMATION_KEY, None)

    # extraction_rules subtree
    er = child.get("extraction_rules")
    if isinstance(er, dict):
        child_er = copy.deepcopy(er)
        er_tr = er.get("transformation_rules")
        if isinstance(er_tr, dict) and isinstance(er_tr.get("fields"), list):
            child_er["transformation_rules"] = {
                **{k: v for k, v in er_tr.items() if k != "fields"},
                "fields": _invert_transformation_fields_list(er_tr.get("fields"), warnings),
            }
        if isinstance(child_er.get("filter_rules"), dict):
            child_er["filter_rules"] = _rebind_filter_rules(child_er["filter_rules"], rename_lower, warnings)
        if child_er.get("deduplication_rules") is not None:
            child_er["deduplication_rules"] = _rebind_deduplication_rules(child_er.get("deduplication_rules"), rename_lower)
        if isinstance(child_er.get("data_cleanup_rules"), dict):
            child_er["data_cleanup_rules"] = _rebind_data_cleanup_rules(child_er["data_cleanup_rules"], rename_lower)
        child["extraction_rules"] = child_er

    if isinstance(child.get("filter_rules"), dict):
        child["filter_rules"] = _rebind_filter_rules(child["filter_rules"], rename_lower, warnings)

    if child.get("deduplication_rules") is not None:
        child["deduplication_rules"] = _rebind_deduplication_rules(child.get("deduplication_rules"), rename_lower)

    if isinstance(child.get("data_cleanup_rules"), dict):
        child["data_cleanup_rules"] = _rebind_data_cleanup_rules(child["data_cleanup_rules"], rename_lower)

    tr_out = get_mapping_transformation(child) or {}
    stats = {
        "enabled": True,
        "mapping_transform_fields_out": len(tr_out.get("fields", []) or []),
        "extraction_rules_present": isinstance(child.get("extraction_rules"), dict),
        "rename_keys": len(rename_lower),
    }
    logger.info(
        "[REVERSE LEG] migration_rules compiled stats=%s warning_count=%d",
        stats,
        len(warnings),
    )
    return ReverseMigrationRulesCompileResult(
        migration_rules=child,
        warnings=warnings,
        errors=errors,
        stats=stats,
    )
