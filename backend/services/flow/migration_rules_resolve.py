"""Resolve migration_rules (extraction + mapping) for save-flow from chat, DB, and session."""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from utils.extraction_rule_pipeline import extraction_rules_from_ai_record
from utils.migration_rules_utils import MAPPING_TRANSFORMATION_KEY, get_mapping_transformation
from utils.rule_accumulation import extract_source_schema_from_data
from utils.rule_merge import merge_extraction_rules_baseline


def _score_extraction_rules(rules: Optional[Dict[str, Any]]) -> int:
    if not isinstance(rules, dict) or not rules:
        return 0
    score = 0
    for key in (
        "filter_rules",
        "deduplication_rules",
        "transformation_rules",
        "data_cleanup_rules",
        "ordering_rules",
    ):
        val = rules.get(key)
        if not isinstance(val, dict) or not val:
            continue
        score += 2
        if key == "filter_rules":
            conds = val.get("conditions")
            if isinstance(conds, list):
                score += len(conds)
        elif key == "transformation_rules":
            fields = val.get("fields")
            if isinstance(fields, list):
                score += len(fields)
        elif key == "data_cleanup_rules":
            score += len(val)
    if rules.get("source_schema"):
        score += 1
    return score


def _coerce_rules(val: Any) -> Optional[Dict[str, Any]]:
    if isinstance(val, dict) and val:
        return val
    return None


def merge_richest_extraction_rules(candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Merge extraction rule dicts; later higher-scored candidates win on conflicts."""
    ordered = sorted(
        [c for c in candidates if isinstance(c, dict) and c],
        key=_score_extraction_rules,
    )
    merged: Dict[str, Any] = {}
    for cand in ordered:
        merged = merge_extraction_rules_baseline(merged, cand)
    return merged


def collect_extraction_rule_candidates(
    *,
    row_meta: Optional[Dict[str, Any]] = None,
    all_records: Optional[List[Dict[str, Any]]] = None,
    extraction_events: Optional[List[Dict[str, Any]]] = None,
    migration_event: Optional[Dict[str, Any]] = None,
    ctx_migration_rules: Optional[Dict[str, Any]] = None,
    lineage_row_metas: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []

    for meta in lineage_row_metas or []:
        rules = _coerce_rules((meta or {}).get("extraction_rules"))
        if rules:
            candidates.append(rules)

    rules = _coerce_rules((row_meta or {}).get("extraction_rules"))
    if rules:
        candidates.append(rules)

    if all_records:
        embedded = extraction_rules_from_ai_record(
            all_records[0] if isinstance(all_records[0], dict) else {}
        )
        if embedded:
            candidates.append(embedded)

    for evt in extraction_events or []:
        rules = _coerce_rules((evt or {}).get("extraction_rules"))
        if rules:
            candidates.append(rules)

    if migration_event:
        rules = _coerce_rules(migration_event.get("extraction_rules"))
        if rules:
            candidates.append(rules)

    ctx_rules = _coerce_rules((ctx_migration_rules or {}).get("extraction_rules"))
    if ctx_rules:
        candidates.append(ctx_rules)

    return candidates


def resolve_mapping_transformation(
    *,
    ctx_transformation_rules: Optional[Dict[str, Any]] = None,
    ctx_migration_rules: Optional[Dict[str, Any]] = None,
    map_event: Optional[Dict[str, Any]] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    for src in (
        (mapping_draft or {}).get("transformation_rules"),
        (map_event or {}).get("transformation_rules"),
        get_mapping_transformation(map_event or {}),
        ctx_transformation_rules,
        get_mapping_transformation(ctx_migration_rules or {}),
    ):
        if isinstance(src, dict) and src.get("fields"):
            return copy.deepcopy(src)
        if isinstance(src, dict) and src:
            return copy.deepcopy(src)
    return None


def resolve_missing_field_preference(
    *,
    ctx_migration_rules: Optional[Dict[str, Any]] = None,
    map_event: Optional[Dict[str, Any]] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
    migration_event: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    for src in (
        (mapping_draft or {}).get("missing_field_preference"),
        (map_event or {}).get("missing_field_preference"),
        (ctx_migration_rules or {}).get("missing_field_preference"),
        (migration_event or {}).get("missing_field_preference"),
    ):
        if isinstance(src, dict) and src.get("option"):
            return copy.deepcopy(src)
    return None


def _source_schema_from_records(all_records: Optional[List[Dict[str, Any]]]) -> List[str]:
    if not all_records:
        return []
    try:
        from post_extract.session import _strip_rule_metadata

        clean = [
            _strip_rule_metadata(r) if isinstance(r, dict) else r for r in all_records
        ]
        return extract_source_schema_from_data(clean)
    except Exception:
        return extract_source_schema_from_data(all_records)


def build_flow_migration_rules(
    *,
    ctx_migration_rules: Optional[Dict[str, Any]] = None,
    ctx_transformation_rules: Optional[Dict[str, Any]] = None,
    extraction_rules: Optional[Dict[str, Any]] = None,
    map_event: Optional[Dict[str, Any]] = None,
    mapping_draft: Optional[Dict[str, Any]] = None,
    migration_event: Optional[Dict[str, Any]] = None,
    all_records: Optional[List[Dict[str, Any]]] = None,
    source_type: str = "file",
) -> Dict[str, Any]:
    """Assemble migration_rules JSON for migration_flows.migration_rules."""
    out = dict(ctx_migration_rules or {})

    ex_rules = dict(extraction_rules or {})
    if all_records and not ex_rules.get("source_schema"):
        schema = _source_schema_from_records(all_records)
        if schema:
            ex_rules.setdefault("source_schema", schema)
    if ex_rules:
        out["extraction_rules"] = ex_rules

    if source_type == "file" and not out.get("source_schema"):
        schema = ex_rules.get("source_schema")
        if not schema and all_records:
            schema = _source_schema_from_records(all_records)
        if schema:
            out["source_schema"] = list(schema)

    mapping_transform = resolve_mapping_transformation(
        ctx_transformation_rules=ctx_transformation_rules,
        ctx_migration_rules=ctx_migration_rules,
        map_event=map_event,
        mapping_draft=mapping_draft,
    )
    if mapping_transform:
        out[MAPPING_TRANSFORMATION_KEY] = mapping_transform
        out.pop("transformation_rules", None)

    pref = resolve_missing_field_preference(
        ctx_migration_rules=ctx_migration_rules,
        map_event=map_event,
        mapping_draft=mapping_draft,
        migration_event=migration_event,
    )
    if pref:
        out["missing_field_preference"] = pref

    return out
