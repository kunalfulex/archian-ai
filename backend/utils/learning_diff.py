"""Normalized diffs for extraction rules and mapping configs (no raw row data)."""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional, Set


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, default=str)


def _rule_field_keys(rules: Dict[str, Any], key: str) -> Set[str]:
    block = rules.get(key) or {}
    if key == "transformation_rules":
        fields = block.get("fields") if isinstance(block, dict) else []
        if not isinstance(fields, list):
            return set()
        out: Set[str] = set()
        for f in fields:
            if not isinstance(f, dict):
                continue
            rid = f.get("rule_id") or f.get("output_field") or f.get("source_field")
            if rid:
                out.add(str(rid))
        return out
    if key == "filter_rules":
        conds = block.get("conditions") if isinstance(block, dict) else []
        if not isinstance(conds, list):
            return set()
        return {str(i) for i in range(len(conds))}
    if key == "data_cleanup_rules" and isinstance(block, dict):
        return set(block.keys())
    return set()


def diff_extraction_rules(
    ai_rules: Optional[Dict[str, Any]],
    final_rules: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Rule-aware diff for extraction_rules buckets."""
    ai = ai_rules or {}
    final = final_rules or {}
    if _stable_json(ai) == _stable_json(final):
        return {"changed": False, "sections": {}}

    sections: Dict[str, Any] = {}
    for key in (
        "filter_rules",
        "transformation_rules",
        "deduplication_rules",
        "data_cleanup_rules",
        "ordering_rules",
        "source_schema",
    ):
        a_block = ai.get(key)
        f_block = final.get(key)
        if _stable_json(a_block) == _stable_json(f_block):
            continue
        ai_keys = _rule_field_keys(ai, key) if key in ("transformation_rules", "filter_rules", "data_cleanup_rules") else set()
        final_keys = _rule_field_keys(final, key) if key in ("transformation_rules", "filter_rules", "data_cleanup_rules") else set()
        sections[key] = {
            "added": sorted(final_keys - ai_keys),
            "removed": sorted(ai_keys - final_keys),
            "modified": _stable_json(a_block) != _stable_json(f_block),
        }

    return {"changed": bool(sections), "sections": sections}


def diff_mapping_config(
    ai_mapping: Optional[Dict[str, Any]],
    final_mapping: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Diff field assignment keys between AI and user-final mapping configs."""
    ai = ai_mapping or {}
    final = final_mapping or {}
    ai_keys = set(ai.keys()) if isinstance(ai, dict) else set()
    final_keys = set(final.keys()) if isinstance(final, dict) else set()
    changed_keys: List[str] = []
    for k in ai_keys | final_keys:
        if _stable_json(ai.get(k)) != _stable_json(final.get(k)):
            changed_keys.append(k)
    return {
        "changed": bool(changed_keys) or ai_keys != final_keys,
        "added_fields": sorted(final_keys - ai_keys),
        "removed_fields": sorted(ai_keys - final_keys),
        "changed_fields": sorted(changed_keys),
    }


def diff_has_signal(diff: Optional[Dict[str, Any]], *, min_changed_fields: int = 1) -> bool:
    if not diff:
        return False
    if diff.get("changed") is False:
        return False
    sections = diff.get("sections") or {}
    if sections:
        return True
    changed = diff.get("changed_fields") or []
    added = diff.get("added_fields") or []
    removed = diff.get("removed_fields") or []
    return len(changed) + len(added) + len(removed) >= min_changed_fields


def redact_snapshot_for_storage(snapshot: Any, max_depth: int = 6) -> Any:
    """Strip list-of-records payloads; keep rules/mapping structure only."""
    if max_depth <= 0:
        return "[truncated]"
    if isinstance(snapshot, list):
        if snapshot and isinstance(snapshot[0], dict) and any(
            k in snapshot[0]
            for k in ("filter_rules", "transformation_rules", "data_cleanup_rules")
        ):
            return redact_snapshot_for_storage(snapshot[0], max_depth - 1)
        return {"_list_len": len(snapshot), "_note": "row_data_omitted"}
    if not isinstance(snapshot, dict):
        return snapshot
    out: Dict[str, Any] = {}
    skip_keys = {"extracted_data", "records", "source_data", "mapped_data", "salesforce_results"}
    for k, v in snapshot.items():
        if k in skip_keys:
            continue
        if k == "data" and isinstance(v, list) and len(v) > 3:
            out[k] = {"_list_len": len(v), "_note": "row_data_omitted"}
            continue
        out[k] = redact_snapshot_for_storage(v, max_depth - 1)
    return out


def copy_rules_snapshot(rules: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rules:
        return None
    return redact_snapshot_for_storage(copy.deepcopy(rules))
