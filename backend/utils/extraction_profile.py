"""
Extraction profile: controls how aggressively extraction_rules are applied at extract time.

Layering (do not conflate):
- **Extract (migrate, no data ops)** → ``source_true_migrate``: source column names only;
  no merge/split/rename to destination headers.
- **Post-extract / data ops** → ``explicit_ops`` (or default): full ``extraction_rules`` including
  merge/combine, split_*, filter, dedupe — applied via ``PostExtractSession`` on stored v1 rows
  whose ``source_schema`` still matches source fields.
- **Map / migrate** → ``mapping_transformation``: destination combine/split (e.g. first_name +
  last_name → sheet ``Name``).

``sanitize_extraction_rules_for_profile`` is only used on the extract path, not in
``post_extract/orchestrator.py``.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ExtractionProfile(str, Enum):
    SOURCE_TRUE_MIGRATE = "source_true_migrate"
    EXPLICIT_OPS = "explicit_ops"
    SAVED_FLOW = "saved_flow"
    DEFAULT = "default"


_MIGRATE_PIPELINE_LABEL_MARKERS = ("map", "migrate")


def _goal_implies_source_true_migrate(goal: Optional[Dict[str, Any]]) -> bool:
    """True when goal pipeline is extract → map/migrate without embedded data ops."""
    if not goal:
        return False
    from services.planning.goal_parser import resolve_data_operations, split_pipeline_and_data_operations

    pipeline, _embedded = split_pipeline_and_data_operations(list(goal.get("operations") or []))
    if "extract" not in pipeline:
        return False
    if not any(o in pipeline for o in ("map", "migrate")):
        return False
    if resolve_data_operations({}, goal=goal):
        return False
    return True


def _pipeline_label_implies_source_true_migrate(pipeline_label: Optional[str]) -> bool:
    pl = (pipeline_label or "").strip().lower()
    if not pl or pl == "empty":
        return False
    if "data_ops" in pl:
        return False
    return "extract" in pl and any(m in pl for m in _MIGRATE_PIPELINE_LABEL_MARKERS)


def _saved_rules_non_empty(saved: Optional[Dict[str, Any]]) -> bool:
    if not saved or not isinstance(saved, dict):
        return False
    for key in (
        "filter_rules",
        "deduplication_rules",
        "transformation_rules",
        "data_cleanup_rules",
        "ordering_rules",
    ):
        val = saved.get(key)
        if not val:
            continue
        if key == "filter_rules" and isinstance(val, dict):
            if val.get("conditions"):
                return True
        elif key == "deduplication_rules" and isinstance(val, dict):
            if val.get("enabled"):
                return True
        elif key == "transformation_rules":
            fields = val.get("fields") if isinstance(val, dict) else val
            if isinstance(fields, list) and fields:
                return True
        elif isinstance(val, dict) and val:
            return True
    return False


def resolve_extraction_profile(
    *,
    saved_extraction_rules: Optional[Dict[str, Any]] = None,
    extraction_profile: Optional[str] = None,
    prompt: Optional[str] = None,
    template: Optional[str] = None,
    intent: Optional[str] = None,
    source: Optional[str] = None,
    goal: Optional[Dict[str, Any]] = None,
) -> ExtractionProfile:
    """Pick how extract-time rules are sanitized and applied."""
    if extraction_profile:
        try:
            return ExtractionProfile(str(extraction_profile).strip().lower())
        except ValueError:
            pass

    if intent == "cleanup":
        return ExtractionProfile.EXPLICIT_OPS

    if _saved_rules_non_empty(saved_extraction_rules):
        return ExtractionProfile.SAVED_FLOW

    if goal:
        from services.planning.goal_parser import resolve_data_operations

        if resolve_data_operations({}, prompt=None, goal=goal):
            return ExtractionProfile.EXPLICIT_OPS
        if _goal_implies_source_true_migrate(goal):
            return ExtractionProfile.SOURCE_TRUE_MIGRATE

    pipeline_label = (template or "").strip().lower()
    if "data_ops" in pipeline_label:
        return ExtractionProfile.EXPLICIT_OPS
    if _pipeline_label_implies_source_true_migrate(pipeline_label):
        return ExtractionProfile.SOURCE_TRUE_MIGRATE

    src = (source or "").strip().lower()
    if src in ("sql", "salesforce", "google_sheets", "gsheets"):
        return ExtractionProfile.SOURCE_TRUE_MIGRATE

    return ExtractionProfile.DEFAULT


def _schema_lower(source_schema: Optional[List[str]]) -> set:
    if not source_schema:
        return set()
    return {str(s).strip().lower() for s in source_schema if str(s).strip()}


def _rule_source_names(rule: Dict[str, Any]) -> List[str]:
    raw = rule.get("source_field") or rule.get("source_fields") or []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if isinstance(x, str) and str(x).strip()]
    return []


def _normalize_duplicate_keep_output_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop conflicting keep rules that share the same output_field (prevents last-wins overwrite)."""
    by_output: Dict[str, List[Dict[str, Any]]] = {}
    passthrough: List[Dict[str, Any]] = []
    for rule in fields:
        if not isinstance(rule, dict):
            continue
        act = (rule.get("action") or "keep").strip().lower()
        if act != "keep":
            passthrough.append(rule)
            continue
        out = (rule.get("output_field") or "").strip()
        if not out:
            passthrough.append(rule)
            continue
        key = out.lower()
        by_output.setdefault(key, []).append(rule)

    kept: List[Dict[str, Any]] = list(passthrough)
    for out_key, group in by_output.items():
        sources = []
        for r in group:
            sources.extend(_rule_source_names(r))
        unique_sources = {s.lower() for s in sources}
        if len(group) == 1:
            kept.append(group[0])
        elif len(unique_sources) <= 1:
            kept.append(group[0])
        else:
            logger.warning(
                "[extraction_profile] Dropping %s keep rules with same output_field %r; use map combine",
                len(group),
                out_key,
            )
    return kept


def sanitize_extraction_rules_for_profile(
    rules: Optional[Dict[str, Any]],
    profile: ExtractionProfile,
    source_schema: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    For source_true_migrate: strip filters/dedupe/cleanup and non-identity transforms.
    Other profiles: return a shallow copy unchanged (callers may still run validate sanitizers).
    """
    if not rules or not isinstance(rules, dict):
        return {}
    if profile != ExtractionProfile.SOURCE_TRUE_MIGRATE:
        return dict(rules)

    out = dict(rules)
    out.pop("filter_rules", None)
    out.pop("deduplication_rules", None)
    out.pop("data_cleanup_rules", None)
    out.pop("ordering_rules", None)

    schema_l = _schema_lower(source_schema)
    tr = out.get("transformation_rules")
    if not isinstance(tr, dict):
        out.pop("transformation_rules", None)
        if source_schema:
            out["source_schema"] = list(source_schema)
        return out

    raw_fields = tr.get("fields")
    if not isinstance(raw_fields, list):
        out.pop("transformation_rules", None)
        if source_schema:
            out["source_schema"] = list(source_schema)
        return out

    kept_fields: List[Dict[str, Any]] = []
    for rule in raw_fields:
        if not isinstance(rule, dict):
            continue
        act = (rule.get("action") or "keep").strip().lower().replace("-", "_")
        if act == "exclude":
            kept_fields.append(dict(rule))
            continue
        if act == "keep":
            srcs = _rule_source_names(rule)
            out_name = (rule.get("output_field") or "").strip()
            if not srcs:
                continue
            if len(srcs) > 1:
                logger.warning("[extraction_profile] Dropping multi-source keep at extract: %s", srcs)
                continue
            src = srcs[0]
            if schema_l and src.lower() not in schema_l:
                continue
            if out_name and out_name.lower() != src.lower():
                logger.warning(
                    "[extraction_profile] Dropping keep rename %r -> %r at extract",
                    src,
                    out_name,
                )
                continue
            kept_fields.append(
                {
                    "source_field": src,
                    "action": "keep",
                    "output_field": src,
                }
            )
            continue
        logger.warning("[extraction_profile] Dropping transform action %r at extract", act)

    kept_fields = _normalize_duplicate_keep_output_fields(kept_fields)
    if kept_fields:
        out["transformation_rules"] = {"fields": kept_fields}
    else:
        out.pop("transformation_rules", None)

    if source_schema:
        out["source_schema"] = list(source_schema)
    return out


def profile_allows_pre_openai_rule_apply(profile: ExtractionProfile) -> bool:
    """Early filter/transform on raw rows before the extraction LLM call."""
    return profile in (ExtractionProfile.SAVED_FLOW, ExtractionProfile.EXPLICIT_OPS, ExtractionProfile.DEFAULT)
