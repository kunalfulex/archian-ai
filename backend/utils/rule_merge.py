"""
Per-operation merge policies for post-extract rule accumulation.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from utils.extraction_rule_pipeline import (
    is_exclude_invalid_email_row_semantics,
    normalize_transformation_rule,
)
from utils.extraction_rules_validate import (
    sanitize_data_cleanup_rules,
    sanitize_filter_rules_dict,
    sanitize_ordering_rules,
    sanitize_transformation_fields,
)


def _scalar_target_field_from_llm(rule: Dict[str, Any]) -> Optional[str]:
    """
    LLMs often use ``field`` / ``source_field`` OR a single-entry ``source_fields`` array
    for prepend/append/combine-style ops. Return one canonical column name or None.
    """
    fld = rule.get("field") or rule.get("source_field")
    if isinstance(fld, str) and fld.strip():
        return fld.strip()
    srcs = rule.get("source_fields")
    if isinstance(srcs, list) and len(srcs) == 1:
        s0 = srcs[0]
        if isinstance(s0, str) and s0.strip():
            return s0.strip()
    if isinstance(srcs, str) and srcs.strip():
        return srcs.strip()
    return None


def _transform_dedupe_key(rule: Dict[str, Any]) -> Optional[str]:
    action = (rule.get("action") or "").lower()
    if action == "exclude":
        sf = rule.get("source_field")
        return f"exclude:{sf}" if sf else None
    if action == "keep_only":
        cols = rule.get("fields") or []
        if isinstance(cols, list) and cols:
            return "keep_only:" + ",".join(
                sorted(str(c).strip().lower() for c in cols if str(c).strip())
            )
        return None
    if action == "keep":
        of = rule.get("output_field") or rule.get("source_field")
        return f"keep:{of}" if of else None
    if action in ("merge", "combine"):
        srcs = rule.get("source_fields") or rule.get("source_field") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        of = rule.get("output_field") or rule.get("target_field")
        if not of:
            return None
        of_key = str(of).strip().lower()
        src_key = ",".join(
            sorted(str(s).strip().lower() for s in srcs if s is not None and str(s).strip())
        )
        return f"merge:{of_key}:{src_key}" if of_key and src_key else None
    if action in ("prepend", "append"):
        sf = rule.get("source_field")
        val = rule.get("value")
        of = rule.get("output_field") or sf
        if isinstance(sf, str) and sf.strip():
            return f"{action}:{sf.strip()}:{of}:{val}"
        return None
    if action == "regex_replace":
        sf = rule.get("source_field")
        pat = rule.get("pattern")
        if isinstance(sf, str) and sf.strip() and isinstance(pat, str) and pat.strip():
            return f"regex_replace:{sf.strip()}:{pat.strip()}"
        return None
    if action.startswith("split") or action == "split_address":
        src = rule.get("source_field")
        outs = rule.get("output_fields") or []
        return f"split:{src}:{','.join(str(o) for o in outs)}" if src else None
    return None


def merge_transformation_rules(
    existing_fields: List[Dict[str, Any]],
    new_fields: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    out = sanitize_transformation_fields(
        [copy.deepcopy(r) for r in existing_fields if isinstance(r, dict)]
    )
    seen: Set[str] = set()
    for r in out:
        k = _transform_dedupe_key(r)
        if k:
            seen.add(k)
    max_order = max([int(r.get("order", -1)) for r in out], default=-1)
    for rule in new_fields:
        if not isinstance(rule, dict):
            continue
        norm = normalize_transformation_rule(rule)
        k = _transform_dedupe_key(norm)
        if k and k in seen:
            continue
        if k:
            seen.add(k)
        max_order += 1
        tagged = copy.deepcopy(norm)
        tagged.setdefault("rule_id", f"rule_{uuid.uuid4().hex[:8]}")
        tagged.setdefault("operation_type", "post_extract")
        tagged.setdefault("timestamp", datetime.utcnow().isoformat())
        tagged.setdefault("order", max_order)
        out.append(tagged)
    return sanitize_transformation_fields(out)


_TRANSFORM_RULE_META_KEYS = frozenset({"fields", "source_schema"})


def _flatten_llm_transformation_rules_dict(tr: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    LLMs sometimes emit top-level buckets instead of a fields array, e.g.:
      {"merge": {...}, "regex_replace": {...}}
    Coerce each bucket into one rule dict with an action.
    """
    def _bucket_items(bucket_val: Any) -> List[Dict[str, Any]]:
        if isinstance(bucket_val, dict):
            return [bucket_val]
        if isinstance(bucket_val, list):
            return [v for v in bucket_val if isinstance(v, dict)]
        return []

    out: List[Dict[str, Any]] = []
    for key, val in tr.items():
        if key in _TRANSFORM_RULE_META_KEYS:
            continue
        bucket_dicts = _bucket_items(val)
        if not bucket_dicts:
            continue
        k = str(key).strip().lower().replace("-", "_")
        if k in ("merge", "combine"):
            for item in bucket_dicts:
                row = {**item, "action": "merge"}
                tf = row.get("target_field")
                if isinstance(tf, str) and tf.strip() and not row.get("output_field"):
                    row["output_field"] = tf.strip()
                out.append(row)
            continue
        if k == "regex_replace":
            for item in bucket_dicts:
                row = {**item, "action": "regex_replace"}
                fld = row.get("field") or row.get("source_field")
                if isinstance(fld, str) and fld.strip():
                    fs = fld.strip()
                    row["source_field"] = fs
                    row.setdefault("output_field", fs)
                out.append(row)
            continue
        if k in ("add_prefix", "prepend"):
            for item in bucket_dicts:
                row = {**item, "action": "add_prefix" if k == "add_prefix" else "prepend"}
                out.append(row)
            continue
        if k in ("add_suffix", "append"):
            for item in bucket_dicts:
                row = {**item, "action": "add_suffix" if k == "add_suffix" else "append"}
                out.append(row)
            continue
        if k == "transform":
            for item in bucket_dicts:
                out.append({**item, "action": "transform"})
            continue
        if k in ("split", "split_address"):
            for item in bucket_dicts:
                out.append({**item, "action": "split" if k == "split" else "split_address"})
            continue
    return out


def _field_names_to_keep_rules(names: List[Any]) -> List[Dict[str, Any]]:
    """LLMs often return {\"fields\": [\"Email\", \"FirstName\", ...]} to drop other columns."""
    cols: List[str] = []
    for name in names:
        if isinstance(name, str) and name.strip():
            cols.append(name.strip())
    if not cols:
        return []
    return [{"action": "keep_only", "fields": cols}]


def _coerce_transform_fields_from_delta(tr: Any) -> List[Dict[str, Any]]:
    """LLM deltas may use transformation_rules as dict.fields, top-level buckets, or a bare list."""
    if isinstance(tr, list):
        dicts = [r for r in tr if isinstance(r, dict)]
        if dicts:
            return dicts
        return _field_names_to_keep_rules(tr)
    if isinstance(tr, dict):
        flds = tr.get("fields")
        if isinstance(flds, list) and flds:
            dicts = [r for r in flds if isinstance(r, dict)]
            if dicts:
                return dicts
            keep_rules = _field_names_to_keep_rules(flds)
            if keep_rules:
                return keep_rules
        # Filter-step LLM shape: { "operations": [ { "field", "operation", "value" } ] }
        ops = tr.get("operations")
        if isinstance(ops, list) and ops:
            return [r for r in ops if isinstance(r, dict)]
        nested = _flatten_llm_transformation_rules_dict(tr)
        if nested:
            return nested
        if isinstance(flds, list):
            return []
    return []


def _normalize_llm_transform_rule_shapes(
    fields_new: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Map LLM-only actions into executor-supported shapes.
    Returns (transformation_field_rules, synthetic_filter_conditions).
    """
    norm_fields: List[Dict[str, Any]] = []
    filter_conditions: List[Dict[str, Any]] = []
    for rule in fields_new:
        if isinstance(rule, dict):
            rule = dict(rule)
            if not (rule.get("action") or "").strip():
                op = rule.get("operation")
                if isinstance(op, str) and op.strip():
                    rule["action"] = op.strip().lower().replace("-", "_")
        if isinstance(rule, dict) and is_exclude_invalid_email_row_semantics(rule):
            sf = rule.get("source_field")
            if isinstance(sf, list):
                sf = sf[0] if sf else None
            if isinstance(sf, str) and sf.strip():
                filter_conditions.append({"field": sf.strip(), "operator": "valid_email"})
            continue
        action = (rule.get("action") or "").strip().lower().replace("-", "_")
        if action in (
            "remove",
            "remove_field",
            "remove_column",
            "drop_field",
            "delete_column",
        ):
            fields_list = rule.get("fields")
            if isinstance(fields_list, list) and fields_list:
                base = {k: v for k, v in rule.items() if k not in ("fields", "action", "source_field")}
                for f in fields_list:
                    if isinstance(f, str) and f.strip():
                        norm_fields.append({**base, "action": "exclude", "source_field": f.strip()})
                continue
            norm_fields.append(
                {
                    **rule,
                    "action": "exclude",
                    "source_field": rule.get("source_field"),
                }
            )
            continue
        if action == "exclude":
            fields_list = rule.get("fields")
            if isinstance(fields_list, list) and fields_list and not rule.get("source_field"):
                base = {k: v for k, v in rule.items() if k not in ("fields",)}
                for f in fields_list:
                    if isinstance(f, str) and f.strip():
                        norm_fields.append({**base, "action": "exclude", "source_field": f.strip()})
                continue
        if action == "add_prefix":
            fld = _scalar_target_field_from_llm(rule)
            pv = rule.get("prefix")
            if pv is None:
                pv = rule.get("value")
            if isinstance(fld, str) and fld.strip():
                norm_fields.append(
                    {
                        "action": "prepend",
                        "source_field": fld.strip(),
                        "output_field": (rule.get("output_field") or fld.strip()),
                        "value": str(pv if pv is not None else ""),
                    }
                )
            continue
        if action == "add_suffix":
            fld = _scalar_target_field_from_llm(rule)
            sv = rule.get("suffix")
            if sv is None:
                sv = rule.get("value")
            if isinstance(fld, str) and fld.strip():
                norm_fields.append(
                    {
                        "action": "append",
                        "source_field": fld.strip(),
                        "output_field": (rule.get("output_field") or fld.strip()),
                        "value": str(sv if sv is not None else ""),
                    }
                )
            continue
        if action == "prepend":
            fld = _scalar_target_field_from_llm(rule)
            pv = rule.get("value")
            if pv is None:
                pv = rule.get("prefix")
            if isinstance(fld, str) and fld.strip():
                norm_fields.append(
                    {
                        "action": "prepend",
                        "source_field": fld.strip(),
                        "output_field": (rule.get("output_field") or fld.strip()),
                        "value": str(pv if pv is not None else ""),
                    }
                )
            continue
        if action == "append":
            fld = _scalar_target_field_from_llm(rule)
            sv = rule.get("value")
            if sv is None:
                sv = rule.get("suffix")
            if isinstance(fld, str) and fld.strip():
                norm_fields.append(
                    {
                        "action": "append",
                        "source_field": fld.strip(),
                        "output_field": (rule.get("output_field") or fld.strip()),
                        "value": str(sv if sv is not None else ""),
                    }
                )
            continue
        if action == "regex_replace":
            fld = _scalar_target_field_from_llm(rule)
            pat = rule.get("pattern")
            rep = rule.get("replacement")
            if (
                isinstance(fld, str)
                and fld.strip()
                and isinstance(pat, str)
                and pat.strip()
            ):
                fs = fld.strip()
                norm_fields.append(
                    {
                        "action": "regex_replace",
                        "source_field": fs,
                        "output_field": (rule.get("output_field") or fs),
                        "pattern": pat.strip(),
                        "replacement": "" if rep is None else str(rep),
                    }
                )
            continue
        if action == "split":
            srcs = rule.get("source_fields") or rule.get("source_field")
            if isinstance(srcs, str):
                srcs = [srcs]
            outs = rule.get("target_fields") or rule.get("output_fields") or []
            if isinstance(outs, str):
                outs = [outs]
            delim = rule.get("delimiter") or rule.get("split_char") or " "
            if (
                isinstance(srcs, list)
                and srcs
                and isinstance(outs, list)
                and len(outs) >= 2
            ):
                sf = str(srcs[0]).strip()
                out_names = [str(o).strip() for o in outs if str(o).strip()]
                if sf and len(out_names) >= 2:
                    n = min(len(out_names), 10)
                    norm_fields.append(
                        {
                            "action": f"split_{n}",
                            "source_field": sf,
                            "output_fields": out_names[:n],
                            "split_char": str(delim) if delim is not None else " ",
                        }
                    )
                    continue
        if action == "combine":
            srcs = rule.get("source_fields")
            if srcs is None:
                srcs = rule.get("source_field")
            if isinstance(srcs, str):
                srcs = [srcs]
            tf = rule.get("target_field") or rule.get("output_field")
            if isinstance(srcs, list) and srcs and isinstance(tf, str) and tf.strip():
                norm_fields.append(
                    {"action": "merge", "source_fields": list(srcs), "output_field": tf.strip()}
                )
            continue
        if action == "remove_fields":
            lst = rule.get("fields")
            if isinstance(lst, list) and lst:
                base = {k: v for k, v in rule.items() if k not in ("fields", "action")}
                for f in lst:
                    if isinstance(f, str) and f.strip():
                        norm_fields.append({**base, "action": "exclude", "source_field": f.strip()})
                continue
        if action == "transform":
            t_raw = rule.get("transformation")
            sf_raw = rule.get("source_field")
            of_raw = rule.get("output_field")
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
                norm_fields.append(rule)
                continue
            sf = sf_raw.strip()
            of = of_raw.strip() if isinstance(of_raw, str) and of_raw.strip() else sf

            def _emit_regex_replace(pat: str, rep: Any) -> None:
                rep_s = "" if rep is None else str(rep)
                norm_fields.append(
                    {
                        **{
                            k: v
                            for k, v in rule.items()
                            if k not in ("action", "transformation", "pattern", "replacement")
                        },
                        "action": "regex_replace",
                        "source_field": sf,
                        "output_field": of,
                        "pattern": pat.strip(),
                        "replacement": rep_s,
                    }
                )

            pat_top = rule.get("pattern")
            if isinstance(pat_top, str) and pat_top.strip():
                _emit_regex_replace(pat_top, rule.get("replacement"))
                continue

            if isinstance(t_raw, dict):
                pat = t_raw.get("pattern")
                rep = t_raw.get("replacement")
                if isinstance(pat, str) and pat.strip():
                    _emit_regex_replace(pat, rep)
                    continue
                if "prepend" in t_raw:
                    pv = t_raw.get("prepend")
                    norm_fields.append(
                        {
                            "action": "prepend",
                            "source_field": sf,
                            "output_field": of,
                            "value": "" if pv is None else str(pv),
                        }
                    )
                    continue
                if "append" in t_raw:
                    av = t_raw.get("append")
                    norm_fields.append(
                        {
                            "action": "append",
                            "source_field": sf,
                            "output_field": of,
                            "value": "" if av is None else str(av),
                        }
                    )
                    continue

            trans = ""
            val_from_nested: Any = None
            if isinstance(t_raw, dict):
                ty = t_raw.get("type") or t_raw.get("op") or t_raw.get("name")
                trans = (
                    str(ty).strip().lower().replace("-", "_")
                    if ty is not None and str(ty).strip()
                    else ""
                )
                val_from_nested = t_raw.get("value")
                if val_from_nested is None:
                    val_from_nested = t_raw.get("prefix")
                if val_from_nested is None:
                    val_from_nested = t_raw.get("suffix")
            elif isinstance(t_raw, str):
                trans = t_raw.strip().lower().replace("-", "_")
            val = rule.get("value")
            if val_from_nested is not None:
                val = val_from_nested
            if val is None and rule.get("prefix") is not None:
                val = rule.get("prefix")
            if val is None and rule.get("suffix") is not None:
                val = rule.get("suffix")
            if trans in ("prepend", "prefix", "add_prefix"):
                norm_fields.append(
                    {
                        "action": "prepend",
                        "source_field": sf,
                        "output_field": of,
                        "value": str(val) if val is not None else "",
                    }
                )
                continue
            if trans in ("append", "suffix", "add_suffix"):
                norm_fields.append(
                    {
                        "action": "append",
                        "source_field": sf,
                        "output_field": of,
                        "value": str(val) if val is not None else "",
                    }
                )
                continue
            norm_fields.append(rule)
            continue
        if action in ("remove_invalid", "remove_invalid_record", "remove_invalid_records"):
            sf = rule.get("source_field") or rule.get("field") or "Email"
            if isinstance(sf, str) and sf.strip():
                filter_conditions.append({"field": sf.strip(), "operator": "valid_email"})
            continue
        norm_fields.append(rule)
    return norm_fields, filter_conditions


def _filter_condition_dedupe_key(condition: Dict[str, Any]) -> Optional[str]:
    if not isinstance(condition, dict):
        return None
    field = condition.get("field")
    op = condition.get("operator")
    if field is None or op is None:
        return None
    value = condition.get("value")
    try:
        import json

        val_part = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        val_part = str(value)
    return f"{field}:{op}:{val_part}"


def _dedupe_filter_conditions(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for cond in conditions:
        if not isinstance(cond, dict):
            continue
        key = _filter_condition_dedupe_key(cond)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(cond)
    return out


def _coerce_filter_rules_fragment(value: Any) -> Any:
    """Accept dict/list forms and return canonical filter_rules-ish shape."""
    if isinstance(value, list):
        return {"conditions": [c for c in value if isinstance(c, dict)], "logic": "AND"}
    if isinstance(value, dict):
        if isinstance(value.get("conditions"), list):
            return value
        if isinstance(value.get("rules"), list):
            return {"conditions": [c for c in value.get("rules") if isinstance(c, dict)], "logic": value.get("logic") or "AND"}
        # LLM sometimes sends a single condition object.
        if value.get("field") and value.get("operator"):
            return {"conditions": [value], "logic": "AND"}
    return value


def _coerce_dedupe_rules_fragment(value: Any) -> Any:
    """Accept list/single-item forms and coerce to {enabled, fields, strategy}."""
    if isinstance(value, dict):
        if isinstance(value.get("fields"), list):
            out = dict(value)
            out.setdefault("enabled", True)
            out.setdefault("strategy", "keep_first")
            return out
        if value.get("field") and not value.get("fields"):
            out = dict(value)
            out["fields"] = [value.get("field")]
            out.setdefault("enabled", True)
            out.setdefault("strategy", "keep_first")
            return out
        return value
    if isinstance(value, list):
        fields: List[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                fields.append(item.strip())
            elif isinstance(item, dict):
                fld = item.get("field")
                if isinstance(fld, str) and fld.strip():
                    fields.append(fld.strip())
        if fields:
            return {"enabled": True, "fields": fields, "strategy": "keep_first"}
    return value


def _coerce_data_cleanup_rules_fragment(value: Any) -> Any:
    """Accept list form and fold into field-keyed cleanup dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        out: Dict[str, Dict[str, Any]] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            field = item.get("field") or item.get("source_field")
            if not isinstance(field, str) or not field.strip():
                continue
            cfg = item.get("config")
            if not isinstance(cfg, dict):
                cfg = {k: v for k, v in item.items() if k not in ("field", "source_field", "config")}
            if not isinstance(cfg, dict):
                continue
            out[field.strip()] = {**out.get(field.strip(), {}), **cfg}
        return out
    return value


def _coerce_ordering_rules_fragment(value: Any) -> Any:
    """Accept list sort form and coerce to ordering_rules dict."""
    if isinstance(value, list):
        return {"sort": [s for s in value if isinstance(s, dict)]}
    return value


def merge_filter_rules(existing: Any, new: Any) -> Any:
    existing = _coerce_filter_rules_fragment(existing)
    new = _coerce_filter_rules_fragment(new)
    if isinstance(existing, list):
        existing = {"conditions": [c for c in existing if isinstance(c, dict)], "logic": "AND"}
    if isinstance(new, list):
        new = {"conditions": [c for c in new if isinstance(c, dict)], "logic": "AND"}

    if not new:
        if isinstance(existing, dict):
            return sanitize_filter_rules_dict(existing) or {"conditions": [], "logic": "AND"}
        return existing
    if not existing:
        return sanitize_filter_rules_dict(copy.deepcopy(new)) or {"conditions": [], "logic": "AND"}
    if not isinstance(existing, dict) or not isinstance(new, dict):
        return sanitize_filter_rules_dict(copy.deepcopy(new)) if new else {"conditions": [], "logic": "AND"}
    ex_conds = list(existing.get("conditions") or [])
    new_conds = list(new.get("conditions") or [])
    merged = {
        "conditions": _dedupe_filter_conditions(ex_conds + new_conds),
        "logic": new.get("logic") or existing.get("logic") or "AND",
    }
    return sanitize_filter_rules_dict(merged) or {"conditions": [], "logic": "AND"}


def merge_ordering_rules(existing: Any, new: Any) -> Any:
    existing = _coerce_ordering_rules_fragment(existing)
    new = _coerce_ordering_rules_fragment(new)
    if not new:
        if isinstance(existing, dict):
            return sanitize_ordering_rules(existing)
        return existing
    if not existing:
        return sanitize_ordering_rules(copy.deepcopy(new))
    if not isinstance(existing, dict) or not isinstance(new, dict):
        return sanitize_ordering_rules(new) if isinstance(new, dict) else existing
    merged = {**copy.deepcopy(existing), **copy.deepcopy(new)}
    return sanitize_ordering_rules(merged)


def merge_deduplication_rules(existing: Any, new: Any) -> Any:
    existing = _coerce_dedupe_rules_fragment(existing)
    new = _coerce_dedupe_rules_fragment(new)
    if new:
        return copy.deepcopy(new)
    return existing


def merge_data_cleanup_rules(existing: Any, new: Any) -> Dict[str, Any]:
    existing = _coerce_data_cleanup_rules_fragment(existing)
    new = _coerce_data_cleanup_rules_fragment(new)
    if not isinstance(existing, dict):
        existing = {}
    if not isinstance(new, dict):
        return sanitize_data_cleanup_rules(copy.deepcopy(existing))
    out = copy.deepcopy(existing)
    for field, cfg in new.items():
        if field in out and isinstance(out[field], dict) and isinstance(cfg, dict):
            out[field] = {**out[field], **cfg}
        else:
            out[field] = copy.deepcopy(cfg)
    return sanitize_data_cleanup_rules(out)


def normalize_data_ops_delta_shape(delta: Any) -> Dict[str, Any]:
    """Normalize LLM delta fragments to canonical per-bucket shapes."""
    if not isinstance(delta, dict):
        return {}
    out = copy.deepcopy(delta)

    if "transformation_rules" in out:
        tr_fields = _coerce_transform_fields_from_delta(out.get("transformation_rules"))
        if tr_fields:
            out["transformation_rules"] = {"fields": tr_fields}
        elif out.get("transformation_rules") in ({}, [], None):
            out.pop("transformation_rules", None)

    if "filter_rules" in out:
        fr = _coerce_filter_rules_fragment(out.get("filter_rules"))
        if isinstance(fr, dict) and fr:
            out["filter_rules"] = fr
        elif not fr:
            out.pop("filter_rules", None)

    if "deduplication_rules" in out:
        dr = _coerce_dedupe_rules_fragment(out.get("deduplication_rules"))
        if isinstance(dr, dict) and dr:
            out["deduplication_rules"] = dr
        elif not dr:
            out.pop("deduplication_rules", None)

    if "data_cleanup_rules" in out:
        cr = _coerce_data_cleanup_rules_fragment(out.get("data_cleanup_rules"))
        if isinstance(cr, dict) and cr:
            out["data_cleanup_rules"] = cr
        elif not cr:
            out.pop("data_cleanup_rules", None)

    if "ordering_rules" in out:
        orules = _coerce_ordering_rules_fragment(out.get("ordering_rules"))
        if isinstance(orules, dict) and orules:
            out["ordering_rules"] = orules
        elif not orules:
            out.pop("ordering_rules", None)

    return out


def merge_extraction_rules_baseline(
    from_records: Dict[str, Any],
    override: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge chat/DB extraction_rules onto rules embedded in row[0] without duplicating
  buckets or dropping nested transform fields (shallow dict merge would).
    """
    base = copy.deepcopy(from_records) if from_records else {}
    if not override or not isinstance(override, dict):
        return base
    override = normalize_data_ops_delta_shape(override)

    if override.get("filter_rules"):
        base["filter_rules"] = merge_filter_rules(base.get("filter_rules"), override["filter_rules"])

    if override.get("deduplication_rules"):
        base["deduplication_rules"] = merge_deduplication_rules(
            base.get("deduplication_rules"), override["deduplication_rules"]
        )

    if override.get("data_cleanup_rules"):
        base["data_cleanup_rules"] = merge_data_cleanup_rules(
            base.get("data_cleanup_rules"), override.get("data_cleanup_rules")
        )

    if override.get("ordering_rules") is not None:
        merged_o = merge_ordering_rules(base.get("ordering_rules"), override["ordering_rules"])
        if merged_o:
            base["ordering_rules"] = merged_o
        else:
            base.pop("ordering_rules", None)

    o_tr = override.get("transformation_rules")
    if o_tr:
        raw_fields = _coerce_transform_fields_from_delta(o_tr)
        if raw_fields:
            ex_tr = base.get("transformation_rules") or {}
            ex_fields = ex_tr.get("fields") if isinstance(ex_tr, dict) else []
            if not isinstance(ex_fields, list):
                ex_fields = []
            base["transformation_rules"] = {
                "fields": merge_transformation_rules(ex_fields, raw_fields),
            }

    for key, val in override.items():
        if key in (
            "filter_rules",
            "deduplication_rules",
            "data_cleanup_rules",
            "ordering_rules",
            "transformation_rules",
        ):
            continue
        if val is not None:
            base[key] = copy.deepcopy(val)

    return base


def merge_rules_for_operation(
    existing_rules: Dict[str, Any],
    delta: Dict[str, Any],
    operation_id: str,
) -> Dict[str, Any]:
    """Merge delta from one post-extract operation into full extraction_rules."""
    base = copy.deepcopy(existing_rules) if existing_rules else {}
    delta = normalize_data_ops_delta_shape(delta)
    op = (operation_id or "").strip().lower()

    if op in ("transform", "transformation", "multi"):
        tr = delta.get("transformation_rules")
        raw_fields = _coerce_transform_fields_from_delta(tr)
        norm_fields, synthetic_fr_conds = _normalize_llm_transform_rule_shapes(raw_fields)
        if norm_fields:
            ex_tr = base.get("transformation_rules") or {}
            ex_fields = ex_tr.get("fields") if isinstance(ex_tr, dict) else []
            if not isinstance(ex_fields, list):
                ex_fields = []
            merged = merge_transformation_rules(ex_fields, norm_fields)
            base["transformation_rules"] = {"fields": merged}
        if synthetic_fr_conds:
            base["filter_rules"] = merge_filter_rules(
                base.get("filter_rules"),
                {"conditions": synthetic_fr_conds, "logic": "AND"},
            )

    if op in ("filter", "multi"):
        if delta.get("filter_rules"):
            base["filter_rules"] = merge_filter_rules(base.get("filter_rules"), delta["filter_rules"])

        tr_delta_filter = delta.get("transformation_rules")
        if tr_delta_filter:
            raw_tr = _coerce_transform_fields_from_delta(tr_delta_filter)
            norm_tr, synth_fc_tr = _normalize_llm_transform_rule_shapes(raw_tr)
            if norm_tr:
                ex_tr_f = base.get("transformation_rules") or {}
                ex_fields_f = ex_tr_f.get("fields") if isinstance(ex_tr_f, dict) else []
                if not isinstance(ex_fields_f, list):
                    ex_fields_f = []
                base["transformation_rules"] = {
                    "fields": merge_transformation_rules(ex_fields_f, norm_tr),
                }
            if synth_fc_tr:
                base["filter_rules"] = merge_filter_rules(
                    base.get("filter_rules"),
                    {"conditions": synth_fc_tr, "logic": "AND"},
                )

        ma = delta.get("merge_actions") if isinstance(delta.get("merge_actions"), dict) else {}
        drop_cols: List[str] = []
        for lst in (
            ma.get("remove_fields"),
            delta.get("remove_fields"),
        ):
            if isinstance(lst, list):
                for f in lst:
                    if isinstance(f, str) and f.strip():
                        drop_cols.append(f.strip())
        seen_drop: Set[str] = set()
        drop_cols_ordered: List[str] = []
        for c in drop_cols:
            if c not in seen_drop:
                seen_drop.add(c)
                drop_cols_ordered.append(c)
        if drop_cols_ordered:
            excludes = [{"action": "exclude", "source_field": f} for f in drop_cols_ordered]
            ex_tr = base.get("transformation_rules") or {}
            ex_fields = ex_tr.get("fields") if isinstance(ex_tr, dict) else []
            if not isinstance(ex_fields, list):
                ex_fields = []
            base["transformation_rules"] = {
                "fields": merge_transformation_rules(ex_fields, excludes),
            }

        srcs = ma.get("source_fields") if isinstance(ma.get("source_fields"), list) else None
        out_merge = ma.get("output_field") or ma.get("target_field")
        if srcs and isinstance(out_merge, str) and out_merge.strip():
            merge_rule = {
                "action": "merge",
                "source_fields": srcs,
                "output_field": out_merge.strip(),
            }
            ex_tr = base.get("transformation_rules") or {}
            ex_fields = ex_tr.get("fields") if isinstance(ex_tr, dict) else []
            if not isinstance(ex_fields, list):
                ex_fields = []
            base["transformation_rules"] = {
                "fields": merge_transformation_rules(ex_fields, [merge_rule]),
            }

    if op in ("dedupe", "deduplication", "multi"):
        if delta.get("deduplication_rules"):
            base["deduplication_rules"] = merge_deduplication_rules(
                base.get("deduplication_rules"), delta["deduplication_rules"]
            )

    if op in ("validate", "validation", "cleanup_values", "multi"):
        if delta.get("data_cleanup_rules"):
            base["data_cleanup_rules"] = merge_data_cleanup_rules(
                base.get("data_cleanup_rules"), delta.get("data_cleanup_rules")
            )

    if op in ("transform", "transformation", "filter", "multi"):
        if delta.get("ordering_rules") is not None:
            merged_o = merge_ordering_rules(base.get("ordering_rules"), delta["ordering_rules"])
            if merged_o:
                base["ordering_rules"] = merged_o
            else:
                base.pop("ordering_rules", None)

    return base
