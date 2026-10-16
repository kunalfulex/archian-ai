"""
Validate and sanitize extraction_rules fragments (post-extract / data ops).

Phase 0: drop unsupported filter conditions and transform rules at merge/apply time
so LLM typos do not silently become no-ops or lenient passes.

See also: edison-ai/docs/FILTER_OPERATOR_MISSING_FIELD_SEMANTICS.md (missing-field filter behavior).
"""
from __future__ import annotations

import copy
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Operators implemented in handlers.file_handler.matches_condition
ALLOWED_FILTER_OPERATORS: Set[str] = frozenset(
    {
        "is_empty",
        "is_not_empty",
        "is_valid",
        "email_is_valid",
        "valid_email",
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "starts_with",
        "ends_with",
        "greater_than",
        "less_than",
        "greater_than_or_equal",
        "less_than_or_equal",
        "between",
        "in",
        "is_in",
        "not_in",
        "nin",
        "is_not_in",
        "regex_matches",
        "date_between",
        "date_on_or_after",
        "date_on_or_before",
    }
)

# Top-level keys allowed per field in data_cleanup_rules (values may be nested dicts we validate separately)
ALLOWED_DATA_CLEANUP_TOP_KEYS: Set[str] = frozenset(
    {
        "type",
        "normalize_case",
        "handle_empty",
        "fill_missing",
        "trim",
        "regex_replace",
        "input_formats",
        "to_iso",
        "default_region",
        "normalize_country",
        "format",
    }
)

_REGEX_REPLACE_INNER = frozenset({"pattern", "replacement", "max_count"})


def sanitize_filter_rules_dict(rules: Any) -> Optional[Dict[str, Any]]:
    """Return a copy of filter_rules with invalid conditions removed."""
    # LLMs often return a bare array of conditions; coerce before validating.
    if isinstance(rules, list):
        rules = {"conditions": [c for c in rules if isinstance(c, dict)], "logic": "AND"}
    if not isinstance(rules, dict):
        return None
    logic = rules.get("logic") or "AND"
    if not isinstance(logic, str):
        logic = "AND"
    logic = logic.upper()
    if logic not in ("AND", "OR"):
        logic = "AND"
    conds = rules.get("conditions")
    if not isinstance(conds, list):
        return {"conditions": [], "logic": logic}
    kept: List[Dict[str, Any]] = []
    for c in conds:
        if not isinstance(c, dict):
            continue
        field = c.get("field")
        op_raw = c.get("operator")
        op = op_raw.strip().lower() if isinstance(op_raw, str) else ""
        if not field or not isinstance(field, str) or not str(field).strip():
            logger.warning("[extraction_rules_validate] Dropping filter condition: missing field")
            continue
        if not op:
            logger.warning("[extraction_rules_validate] Dropping filter condition: missing operator")
            continue
        if op not in ALLOWED_FILTER_OPERATORS:
            logger.warning("[extraction_rules_validate] Dropping filter condition: unknown operator %r", op)
            continue
        kept.append(copy.deepcopy(c))
    return {"conditions": kept, "logic": logic}


def _transform_action_supported(action: str) -> bool:
    a = (action or "").strip().lower()
    if a in (
        "merge",
        "combine",
        "keep",
        "keep_only",
        "exclude",
        "split_address",
        "prepend",
        "append",
        "regex_replace",
    ):
        return True
    return bool(re.match(r"^split_\d+$", a))


def _coerce_transform_action_for_executor(rule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Persisted / legacy rules may use action \"transform\" with nested shapes the executor
    does not understand directly (pattern+replacement, nested prepend, fields inside transformation).
    Normalize to prepend / append / regex_replace so sanitize does not drop them.
    """
    r = dict(rule)
    act = (r.get("action") or "").strip().lower()
    if not act:
        op = r.get("operation")
        if isinstance(op, str) and op.strip():
            act = op.strip().lower().replace("-", "_")
            r["action"] = act
    # LLM emits merge + nested transformation.pattern on a single column — executor ignores nested transform on merge.
    if act in ("merge", "combine"):
        t_raw = r.get("transformation")
        srcs = r.get("source_fields") or r.get("source_field") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        if (
            isinstance(srcs, list)
            and len(srcs) == 1
            and isinstance(t_raw, dict)
            and isinstance(t_raw.get("pattern"), str)
            and str(t_raw.get("pattern")).strip()
        ):
            sf = str(srcs[0]).strip()
            rep = t_raw.get("replacement")
            rep_s = "" if rep is None else str(rep)
            return {
                "action": "regex_replace",
                "source_field": sf,
                "output_field": (r.get("output_field") or r.get("target_field") or sf),
                "pattern": str(t_raw["pattern"]).strip(),
                "replacement": rep_s,
            }
    if act != "transform":
        return r
    t_raw = r.get("transformation")
    sf_raw = r.get("source_field")
    of_raw = r.get("output_field")
    if isinstance(t_raw, dict):
        if not isinstance(sf_raw, str) or not str(sf_raw).strip():
            ns = t_raw.get("source_field") or t_raw.get("field")
            if isinstance(ns, str) and ns.strip():
                sf_raw = ns.strip()
        if not isinstance(of_raw, str) or not str(of_raw).strip():
            no = t_raw.get("output_field")
            if isinstance(no, str) and no.strip():
                of_raw = no.strip()

    if not isinstance(sf_raw, str) or not sf_raw.strip():
        return r
    sf = sf_raw.strip()
    of = of_raw.strip() if isinstance(of_raw, str) and of_raw.strip() else sf

    pat_top = r.get("pattern")
    if isinstance(pat_top, str) and pat_top.strip():
        rep_top = r.get("replacement")
        rep_s = "" if rep_top is None else str(rep_top)
        out = {
            k: v
            for k, v in r.items()
            if k not in ("action", "transformation", "pattern", "replacement")
        }
        out["action"] = "regex_replace"
        out["source_field"] = sf
        out["output_field"] = of
        out["pattern"] = pat_top.strip()
        out["replacement"] = rep_s
        return out

    if isinstance(t_raw, dict):
        pat = t_raw.get("pattern")
        if isinstance(pat, str) and pat.strip():
            rep = t_raw.get("replacement")
            rep_s = "" if rep is None else str(rep)
            out = {k: v for k, v in r.items() if k not in ("action", "transformation")}
            out["action"] = "regex_replace"
            out["source_field"] = sf
            out["output_field"] = of
            out["pattern"] = pat.strip()
            out["replacement"] = rep_s
            return out
        if "prepend" in t_raw:
            pv = t_raw.get("prepend")
            out = {k: v for k, v in r.items() if k not in ("action", "transformation")}
            out["action"] = "prepend"
            out["source_field"] = sf
            out["output_field"] = of
            out["value"] = "" if pv is None else str(pv)
            return out
        if "append" in t_raw:
            av = t_raw.get("append")
            out = {k: v for k, v in r.items() if k not in ("action", "transformation")}
            out["action"] = "append"
            out["source_field"] = sf
            out["output_field"] = of
            out["value"] = "" if av is None else str(av)
            return out
    return r


def sanitize_transformation_fields(fields: Any) -> List[Dict[str, Any]]:
    """Drop transformation field rules with unsupported actions."""
    if not isinstance(fields, list):
        return []
    out: List[Dict[str, Any]] = []
    for r in fields:
        if not isinstance(r, dict):
            continue
        rr = _coerce_transform_action_for_executor(copy.deepcopy(r))
        act = (rr.get("action") or "").strip().lower()
        if not _transform_action_supported(act):
            logger.warning("[extraction_rules_validate] Dropping transformation rule: unsupported action %r", act)
            continue
        out.append(rr)
    return out


def _sanitize_regex_replace_cfg(cfg: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(cfg, dict):
        return None
    inner = {k: cfg[k] for k in _REGEX_REPLACE_INNER if k in cfg}
    if "pattern" not in inner or "replacement" not in inner:
        return None
    if not isinstance(inner["pattern"], str) or not isinstance(inner["replacement"], str):
        return None
    return inner


def sanitize_data_cleanup_rules(rules: Any) -> Dict[str, Any]:
    """
    Strip unknown top-level keys per field; normalize regex_replace to allowed inner keys only.
    """
    if not isinstance(rules, dict):
        return {}
    out: Dict[str, Any] = {}
    for field, cfg in rules.items():
        if not isinstance(field, str) or not field.strip():
            continue
        if not isinstance(cfg, dict):
            continue
        cleaned: Dict[str, Any] = {}
        for k, v in cfg.items():
            if k not in ALLOWED_DATA_CLEANUP_TOP_KEYS:
                logger.debug("[extraction_rules_validate] Dropping unknown data_cleanup key %r on field %r", k, field)
                continue
            if k == "regex_replace" and isinstance(v, dict):
                inner = _sanitize_regex_replace_cfg(v)
                if inner:
                    cleaned[k] = inner
                continue
            cleaned[k] = copy.deepcopy(v)
        if cleaned:
            out[field] = cleaned
    return out


def _ordering_max_sanitize() -> int:
    try:
        n = int(os.getenv("DATA_OPS_ORDERING_MAX_LIMIT", "500000"))
    except ValueError:
        n = 500000
    return max(1, min(n, 10_000_000))


def strip_ordering_row_limit(rules: Optional[Dict[str, Any]]) -> None:
    """Remove row cap from ordering_rules in place (data-ops should sort full dataset)."""
    if not isinstance(rules, dict):
        return
    ordering = rules.get("ordering_rules")
    if not isinstance(ordering, dict):
        return
    ordering.pop("limit", None)
    if not ordering.get("sort"):
        rules.pop("ordering_rules", None)


def prompt_requests_row_cap(prompt: Optional[str]) -> bool:
    """True when the user explicitly asked to cap row count (top N, first N, limit to N, etc.)."""
    t = (prompt or "").strip().lower()
    if not t:
        return False

    if re.search(r"\b(top|first|only|max|maximum|limit)\s+\d+\b", t):
        return True
    if re.search(r"\b\d+\s+(rows?|records?)\b", t):
        return True
    markers = (
        "limit to",
        "cap at",
        "head(",
        "take ",
        "sample of",
        "subset of",
    )
    return any(m in t for m in markers)


def sanitize_ordering_rules(
    rules: Any, *, include_row_limit: bool = True
) -> Optional[Dict[str, Any]]:
    """
    ordering_rules: { "sort": [ { "field", "direction" } ], "limit": int }.
    When ``include_row_limit`` is False, sort specs are kept but row cap is dropped.
    """
    if not isinstance(rules, dict):
        return None
    out: Dict[str, Any] = {}
    sort_in = rules.get("sort")
    if isinstance(sort_in, list):
        clean_sort: List[Dict[str, str]] = []
        for s in sort_in:
            if not isinstance(s, dict):
                continue
            f = (s.get("field") or s.get("column") or "").strip()
            if not f:
                continue
            d = (s.get("direction") or "asc").strip().lower()
            if d not in ("asc", "desc"):
                d = "asc"
            clean_sort.append({"field": f, "direction": d})
        if clean_sort:
            out["sort"] = clean_sort
    if include_row_limit:
        lim = rules.get("limit")
        if lim is not None:
            try:
                n = int(lim)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                out["limit"] = min(n, _ordering_max_sanitize())
    return out if out else None


def _schema_name_map(fields: List[str]) -> Dict[str, str]:
    """Lowercase field name → canonical casing from schema list."""
    out: Dict[str, str] = {}
    for raw in fields or []:
        if isinstance(raw, str) and raw.strip():
            out[raw.strip().lower()] = raw.strip()
    return out


def normalize_field_token(name: str) -> str:
    """Case/spacing-insensitive key for column name matching."""
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def resolve_field_in_schema(field_name: str, schema: List[str]) -> Optional[str]:
    """Map an LLM field reference to the exact column name in schema, if any."""
    if not isinstance(field_name, str) or not field_name.strip() or not schema:
        return None
    raw = field_name.strip()
    if raw in schema:
        return raw
    canon = _schema_name_map(schema)
    hit = canon.get(raw.lower())
    if hit:
        return hit
    token = normalize_field_token(raw)
    if len(token) < 2:
        return None
    for col in schema:
        if isinstance(col, str) and normalize_field_token(col) == token:
            return col.strip()
    return None


def _resolve_field_list_in_schema(names: Any, schema: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    items = names if isinstance(names, list) else ([names] if isinstance(names, str) else [])
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        resolved = resolve_field_in_schema(item.strip(), schema)
        if resolved and resolved.lower() not in seen:
            seen.add(resolved.lower())
            out.append(resolved)
    return out


def align_transform_rules_with_schema(
    transformation_rules: Any,
    source_schema: List[str],
) -> Any:
    """
    Rewrite transform rule field refs to canonical schema names.
    Drop rules whose source columns cannot be resolved (prevents silent no-ops).
    """
    if not source_schema or not transformation_rules:
        return transformation_rules

    if isinstance(transformation_rules, dict):
        fields = transformation_rules.get("fields")
        if not isinstance(fields, list):
            return transformation_rules
        aligned = align_transform_rules_with_schema(fields, source_schema)
        return {**transformation_rules, "fields": aligned}

    if not isinstance(transformation_rules, list):
        return transformation_rules

    kept: List[Dict[str, Any]] = []
    for rule in transformation_rules:
        if not isinstance(rule, dict):
            continue
        out = copy.deepcopy(rule)
        action = (out.get("action") or "").strip().lower()

        if action in ("merge", "combine"):
            srcs = out.get("source_fields")
            if srcs is None:
                srcs = out.get("source_field")
            resolved = _resolve_field_list_in_schema(srcs, source_schema)
            if not resolved:
                continue
            out["source_fields"] = resolved
            out.pop("source_field", None)
            kept.append(out)
            continue

        if action.startswith("split"):
            sf = out.get("source_field") or out.get("field")
            resolved_sf = (
                resolve_field_in_schema(str(sf), source_schema)
                if isinstance(sf, str) and sf.strip()
                else None
            )
            if not resolved_sf:
                continue
            out["source_field"] = resolved_sf
            kept.append(out)
            continue

        if action in ("keep", "exclude", "regex_replace", "prepend", "append", "transform"):
            sf = out.get("source_field") or out.get("field")
            if isinstance(sf, str) and sf.strip():
                resolved = resolve_field_in_schema(sf.strip(), source_schema)
                if not resolved:
                    continue
                out["source_field"] = resolved
            kept.append(out)
            continue

        kept.append(out)

    return kept


def _transformation_field_rules(transformation_rules: Any) -> List[Dict[str, Any]]:
    if isinstance(transformation_rules, dict):
        fields = transformation_rules.get("fields")
        if isinstance(fields, list):
            return [f for f in fields if isinstance(f, dict)]
    if isinstance(transformation_rules, list):
        return [f for f in transformation_rules if isinstance(f, dict)]
    return []


def fields_removed_by_transform(transformation_rules: Any) -> List[str]:
    """Source columns dropped by merge/exclude rules (canonical names when known)."""
    removed: List[str] = []
    seen: Set[str] = set()
    for rule in _transformation_field_rules(transformation_rules):
        action = (rule.get("action") or "").strip().lower()
        if action in ("merge", "combine"):
            srcs = rule.get("source_fields") or rule.get("source_field") or []
            if isinstance(srcs, str):
                srcs = [srcs]
            if isinstance(srcs, list):
                for s in srcs:
                    if isinstance(s, str) and s.strip():
                        name = s.strip()
                        key = name.lower()
                        if key not in seen:
                            seen.add(key)
                            removed.append(name)
        elif action == "exclude":
            bulk = rule.get("fields")
            targets: List[str] = []
            if isinstance(bulk, list):
                targets.extend(str(f).strip() for f in bulk if isinstance(f, str) and str(f).strip())
            sf = rule.get("source_field") or rule.get("field")
            if isinstance(sf, str) and sf.strip():
                targets.append(sf.strip())
            for name in targets:
                key = name.lower()
                if key not in seen:
                    seen.add(key)
                    removed.append(name)
    return removed


def predict_post_transform_schema(
    source_schema: List[str],
    transformation_rules: Any,
) -> List[str]:
    """
    Predict column names present after transformation_rules run (schema-level only).
    Used to drop filter conditions on merged-away columns when transform runs before filter.
    """
    canonical = _schema_name_map(source_schema)
    present = set(canonical.keys())

    for rule in _transformation_field_rules(transformation_rules):
        action = (rule.get("action") or "").strip().lower()

        if action in ("merge", "combine"):
            srcs = rule.get("source_fields") or rule.get("source_field") or []
            if isinstance(srcs, str):
                srcs = [srcs]
            if isinstance(srcs, list):
                for s in srcs:
                    if isinstance(s, str) and s.strip():
                        present.discard(s.strip().lower())
            out_f = rule.get("output_field") or rule.get("target_field")
            if isinstance(out_f, str) and out_f.strip():
                key = out_f.strip().lower()
                present.add(key)
                canonical[key] = out_f.strip()
            continue

        if action == "exclude":
            bulk = rule.get("fields")
            if isinstance(bulk, list):
                for f in bulk:
                    if isinstance(f, str) and f.strip():
                        present.discard(f.strip().lower())
            sf = rule.get("source_field") or rule.get("field")
            if isinstance(sf, str) and sf.strip():
                present.discard(sf.strip().lower())
            continue

        if action == "keep_only":
            cols = rule.get("fields") or []
            allowed: Dict[str, str] = {}
            for col in cols:
                if isinstance(col, str) and col.strip():
                    key = col.strip().lower()
                    allowed[key] = canonical.get(key, col.strip())
            present = set(allowed.keys())
            canonical.update(allowed)
            continue

        out_f = rule.get("output_field") or rule.get("target_field")
        if isinstance(out_f, str) and out_f.strip():
            key = out_f.strip().lower()
            present.add(key)
            canonical.setdefault(key, out_f.strip())

    return [canonical[k] for k in sorted(present) if k in canonical]


def sanitize_filter_rules_for_post_transform_schema(
    filter_rules: Any,
    post_transform_schema: List[str],
    *,
    removed_field_hints: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Keep only filter conditions on columns that survive transform; normalize field casing.
    """
    base = sanitize_filter_rules_dict(filter_rules)
    if not base:
        return base

    allowed = _schema_name_map(post_transform_schema)
    removed = {
        str(f).strip().lower()
        for f in (removed_field_hints or [])
        if isinstance(f, str) and str(f).strip()
    }

    kept: List[Dict[str, Any]] = []
    for cond in base.get("conditions") or []:
        if not isinstance(cond, dict):
            continue
        field = cond.get("field")
        if not isinstance(field, str) or not field.strip():
            continue
        fl = field.strip().lower()
        if fl in removed:
            logger.info(
                "[extraction_rules_validate] Dropping filter on merged-away field %r",
                field,
            )
            continue
        if fl not in allowed:
            logger.info(
                "[extraction_rules_validate] Dropping filter on field %r not in post-transform schema",
                field,
            )
            continue
        normalized = copy.deepcopy(cond)
        normalized["field"] = allowed[fl]
        kept.append(normalized)

    logic = base.get("logic") or "AND"
    if not kept:
        return {"conditions": [], "logic": logic}
    return {"conditions": kept, "logic": logic}


def align_filter_rules_with_transform(
    filter_rules: Any,
    source_schema: List[str],
    transformation_rules: Any,
) -> Optional[Dict[str, Any]]:
    """Convenience: predict post-transform schema and sanitize filter conditions."""
    if not transformation_rules:
        post = list(source_schema or [])
        removed: List[str] = []
    else:
        post = predict_post_transform_schema(source_schema, transformation_rules)
        removed = fields_removed_by_transform(transformation_rules)
    return sanitize_filter_rules_for_post_transform_schema(
        filter_rules,
        post,
        removed_field_hints=removed,
    )
