"""
Unified extraction rule application order for all flows.

Default bucket order: transformation → filter → ordering → deduplication → data_cleanup.

When ``transform_before_filter`` is False (filter-first plans), filter runs before transforms
so row filters can reference columns that merges later remove.
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Dict, List, Optional

from handlers.file_handler import (
    apply_cleanup_and_validation_rules,
    apply_deduplication_rules,
    apply_filter_rules,
    apply_ordering_rules,
    apply_transformations,
)

_pipeline_log = logging.getLogger("post_extract.pipeline")


def condition_implies_invalid_row_filter(condition: Any) -> bool:
    """True when LLM meant 'drop invalid rows' (e.g. bad emails), not 'drop the column'."""
    if condition is None:
        return False
    s = str(condition).strip().lower()
    return s in ("invalid", "invalid_email", "if_invalid", "bad", "invalid_value")


def field_likely_email_column(field: str) -> bool:
    f = (field or "").strip().lower()
    return "email" in f or f in ("e_mail", "mail")


def is_exclude_invalid_email_row_semantics(rule: Dict[str, Any]) -> bool:
    """
    Detect transformation rules that look like row-level invalid-email removal.
    Executing these as field excludes removes the column before filter_rules run,
    so valid_email on Email would drop every row.
    """
    if not isinstance(rule, dict):
        return False
    action = (rule.get("action") or "").strip().lower().replace("-", "_")
    if action not in (
        "exclude",
        "remove",
        "remove_field",
        "remove_column",
        "drop_field",
        "delete_column",
    ):
        return False
    if not condition_implies_invalid_row_filter(rule.get("condition")):
        return False
    sf = rule.get("source_field")
    if isinstance(sf, list):
        sf = sf[0] if sf else ""
    if not isinstance(sf, str) or not sf.strip():
        return False
    return field_likely_email_column(sf.strip())


def partition_invalid_row_email_excludes_from_transformation_rules(
    transformation_rules: Optional[Dict[str, Any]],
) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split mis-modeled 'exclude invalid email' rules into filter conditions; return cleaned TR + synth conditions."""
    if not transformation_rules or not isinstance(transformation_rules, dict):
        return transformation_rules, []
    fields = transformation_rules.get("fields")
    if not isinstance(fields, list):
        return transformation_rules, []
    kept: List[Dict[str, Any]] = []
    synth: List[Dict[str, Any]] = []
    for r in fields:
        if isinstance(r, dict) and is_exclude_invalid_email_row_semantics(r):
            sf = r.get("source_field")
            if isinstance(sf, list):
                sf = sf[0] if sf else ""
            if isinstance(sf, str) and sf.strip():
                synth.append({"field": sf.strip(), "operator": "valid_email"})
            continue
        kept.append(r)
    if not kept:
        return None, synth
    return {**transformation_rules, "fields": kept}, synth


def _post_extract_debug_enabled() -> bool:
    """Lazy import so importing this module does not run post_extract/__init__.py (circular imports)."""
    from post_extract.debug import post_extract_debug_enabled as _fn

    return _fn()


def _post_extract_log_snapshot(label: str, payload: Any) -> None:
    from post_extract.debug import log_snapshot as _fn

    _fn(label, payload)


def normalize_transformation_rule(rule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a single transformation rule for apply + validation.
    - merge/combine: ensure source_fields list from source_field array or source_fields
    """
    if not isinstance(rule, dict):
        return rule
    out = copy.deepcopy(rule)
    action = (out.get("action") or "").strip().lower().replace("-", "_")
    if action in ("remove", "remove_field", "remove_column", "drop_field", "delete_column"):
        out["action"] = "exclude"
        action = "exclude"
    if action in ("merge", "combine"):
        srcs = out.get("source_fields")
        if srcs is None:
            srcs = out.get("source_field") or []
        if isinstance(srcs, str):
            srcs = [srcs]
        if isinstance(srcs, list):
            out["source_fields"] = [str(s).strip() for s in srcs if str(s).strip()]
        tf = out.get("target_field")
        if tf and not out.get("output_field"):
            out["output_field"] = str(tf).strip()
        if action == "merge" and isinstance(out.get("source_field"), list):
            out.pop("source_field", None)
    if action in ("prepend", "append"):
        # Models mirror combine() shape: action prepend + source_fields: ["Col"] only.
        sf0 = out.get("source_field")
        if not (isinstance(sf0, str) and sf0.strip()):
            alt = out.get("field")
            if isinstance(alt, str) and alt.strip():
                out["source_field"] = alt.strip()
            else:
                srcs = out.get("source_fields")
                if isinstance(srcs, list) and len(srcs) == 1:
                    x = srcs[0]
                    if isinstance(x, str) and x.strip():
                        out["source_field"] = x.strip()
                elif isinstance(srcs, str) and srcs.strip():
                    out["source_field"] = srcs.strip()
        if isinstance(out.get("source_field"), str) and out["source_field"].strip():
            sfv = out["source_field"].strip()
            out["source_field"] = sfv
            of = out.get("output_field")
            tf = out.get("target_field")
            if not (isinstance(of, str) and of.strip()):
                out["output_field"] = (
                    tf.strip() if isinstance(tf, str) and tf.strip() else sfv
                )
            # Executor expects scalar source_field; drop list alias so merge dedupe keys stay stable.
            out.pop("source_fields", None)
    return out


def normalize_transformation_rules(rules: Any) -> Optional[Dict[str, Any]]:
    def _strip_non_transform_fields(raw_fields: List[Any]) -> List[Dict[str, Any]]:
        """Rows filtering rows belong in filter_rules; LLMs often duplicate them here."""
        out: List[Dict[str, Any]] = []
        for r in raw_fields:
            if not isinstance(r, dict):
                continue
            nr = normalize_transformation_rule(r)
            act = (nr.get("action") or "").strip().lower()
            if act == "filter":
                continue
            out.append(nr)
        return out

    if rules is None:
        return None
    if isinstance(rules, list):
        fields = _strip_non_transform_fields([r for r in rules if isinstance(r, dict)])
        return {"fields": fields} if fields else None
    if not isinstance(rules, dict):
        return None
    fields = rules.get("fields")
    if not isinstance(fields, list):
        return rules
    return {
        **{k: v for k, v in rules.items() if k != "fields"},
        "fields": _strip_non_transform_fields(fields),
    }


def apply_extraction_rules(
    data: List[Dict[str, Any]],
    extraction_rules: Optional[Dict[str, Any]],
    *,
    source_schema: Optional[List[str]] = None,
    transform_before_filter: bool = True,
    apply_ordering_limit: bool = True,
) -> List[Dict[str, Any]]:
    """
    Apply all rule buckets.

    ``transform_before_filter`` (default True): historical behavior — column transforms then row filters.
    Set False when the user's plan runs filter before merge/transform so filters still see source columns.

    ``apply_ordering_limit`` (default True): apply ordering_rules.limit when set. Post-extract data ops
    pass False so sort runs on the full dataset unless the user explicitly requested a row cap.
    """
    if not data or not extraction_rules or not isinstance(extraction_rules, dict):
        return data

    processed = list(data)
    rules = dict(extraction_rules)

    from utils.extraction_rules_validate import (
        sanitize_data_cleanup_rules,
        sanitize_filter_rules_dict,
        sanitize_ordering_rules,
        sanitize_transformation_fields,
    )

    if isinstance(rules.get("filter_rules"), dict):
        rules = dict(rules)
        rules["filter_rules"] = sanitize_filter_rules_dict(rules["filter_rules"]) or {
            "conditions": [],
            "logic": "AND",
        }
    if isinstance(rules.get("data_cleanup_rules"), dict):
        rules = dict(rules)
        rules["data_cleanup_rules"] = sanitize_data_cleanup_rules(rules["data_cleanup_rules"])
    if isinstance(rules.get("ordering_rules"), dict):
        rules = dict(rules)
        orules = sanitize_ordering_rules(
            rules["ordering_rules"],
            include_row_limit=apply_ordering_limit,
        )
        if orules:
            rules["ordering_rules"] = orules
        else:
            rules.pop("ordering_rules", None)

    if _post_extract_debug_enabled():
        _post_extract_log_snapshot(
            "pipeline.start",
            {
                "record_count": len(processed),
                "rule_top_keys": list(rules.keys()),
                "filter_rules": rules.get("filter_rules"),
                "transformation_rules": rules.get("transformation_rules"),
                "deduplication_rules": rules.get("deduplication_rules"),
                "data_cleanup_rules_present": bool(rules.get("data_cleanup_rules")),
            },
        )

    transformation_rules = normalize_transformation_rules(rules.get("transformation_rules"))
    synth_email_filters: List[Dict[str, Any]] = []
    if transformation_rules and isinstance(transformation_rules.get("fields"), list):
        transformation_rules, synth_email_filters = (
            partition_invalid_row_email_excludes_from_transformation_rules(transformation_rules)
        )
    if transformation_rules and isinstance(transformation_rules.get("fields"), list):
        transformation_rules = dict(transformation_rules)
        transformation_rules["fields"] = sanitize_transformation_fields(
            transformation_rules["fields"]
        )
        if not transformation_rules["fields"]:
            transformation_rules = None
    if synth_email_filters:
        from utils.rule_merge import merge_filter_rules

        rules = dict(rules)
        rules["filter_rules"] = merge_filter_rules(
            rules.get("filter_rules"),
            {"conditions": synth_email_filters, "logic": "AND"},
        )

    def _phase_transform() -> None:
        nonlocal processed
        if not transformation_rules or not transformation_rules.get("fields"):
            return
        schema = source_schema or rules.get("source_schema")
        if schema and isinstance(schema, list):
            try:
                from utils.rule_accumulation import apply_transformation_rules_in_order

                processed[:] = apply_transformation_rules_in_order(
                    processed, transformation_rules, schema
                )
            except Exception:
                processed[:] = apply_transformations(processed, transformation_rules)
        else:
            processed[:] = apply_transformations(processed, transformation_rules)

        if _post_extract_debug_enabled():
            _pipeline_log.info(
                "[POST_EXTRACT_DEBUG] pipeline.after_transform records=%s fields_sample=%s",
                len(processed),
                sorted({k for r in processed[:20] if isinstance(r, dict) for k in r.keys()})[:40],
            )

    def _phase_filter() -> None:
        nonlocal processed, rules
        filter_rules = rules.get("filter_rules")
        if isinstance(filter_rules, dict) and filter_rules.get("conditions"):
            if transform_before_filter and transformation_rules:
                from utils.extraction_rules_validate import align_filter_rules_with_transform

                schema_for_filter = source_schema or rules.get("source_schema") or []
                if not schema_for_filter and processed:
                    schema_for_filter = sorted(
                        {
                            k
                            for r in processed[:50]
                            if isinstance(r, dict)
                            for k in r.keys()
                        }
                    )
                aligned = align_filter_rules_with_transform(
                    filter_rules,
                    list(schema_for_filter) if isinstance(schema_for_filter, list) else [],
                    transformation_rules,
                )
                if aligned is not None:
                    rules = dict(rules)
                    rules["filter_rules"] = aligned
                    filter_rules = aligned
            if _post_extract_debug_enabled():
                _post_extract_log_snapshot(
                    "pipeline.before_filter",
                    {"filter_rules": filter_rules, "record_count": len(processed)},
                )
            processed[:] = apply_filter_rules(processed, filter_rules)
            if _post_extract_debug_enabled():
                _pipeline_log.info(
                    "[POST_EXTRACT_DEBUG] pipeline.after_filter records=%s", len(processed)
                )

    if transform_before_filter:
        _phase_transform()
        _phase_filter()
    else:
        _phase_filter()
        _phase_transform()

    ordering_rules = rules.get("ordering_rules")
    if isinstance(ordering_rules, dict) and (
        ordering_rules.get("sort") or ordering_rules.get("limit") is not None
    ):
        if _post_extract_debug_enabled():
            _post_extract_log_snapshot(
                "pipeline.before_ordering",
                {"ordering_rules": ordering_rules, "record_count": len(processed)},
            )
        processed = apply_ordering_rules(
            processed,
            ordering_rules,
            apply_row_limit=apply_ordering_limit,
        )
        if _post_extract_debug_enabled():
            _pipeline_log.info("[POST_EXTRACT_DEBUG] pipeline.after_ordering records=%s", len(processed))

    deduplication_rules = rules.get("deduplication_rules")
    if isinstance(deduplication_rules, dict) and deduplication_rules.get("enabled"):
        before_d = len(processed)
        processed = apply_deduplication_rules(processed, deduplication_rules)
        if _post_extract_debug_enabled():
            _pipeline_log.info(
                "[POST_EXTRACT_DEBUG] pipeline.after_dedupe records=%s (was %s)",
                len(processed),
                before_d,
            )

    data_cleanup_rules = rules.get("data_cleanup_rules")
    if isinstance(data_cleanup_rules, dict) and data_cleanup_rules:
        before_c = len(processed)
        mock = [{"data_cleanup_rules": data_cleanup_rules}]
        processed = apply_cleanup_and_validation_rules(processed, mock)
        if _post_extract_debug_enabled():
            _pipeline_log.info(
                "[POST_EXTRACT_DEBUG] pipeline.after_cleanup records=%s (was %s)",
                len(processed),
                before_c,
            )

    return processed


def extraction_rules_from_ai_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """Build extraction_rules dict from first AI metadata record."""
    out: Dict[str, Any] = {}
    if not isinstance(record, dict):
        return out
    for key in (
        "filter_rules",
        "deduplication_rules",
        "transformation_rules",
        "data_cleanup_rules",
        "ordering_rules",
    ):
        val = record.get(key)
        if val:
            out[key] = val
    return out
