"""
Helpers for migration_rules JSON stored on flows.

Top-level mapping field splits/combines use ``mapping_transformation`` (not
``transformation_rules``) to avoid confusion with
``extraction_rules.transformation_rules`` (extraction / data-ops stage).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Optional

MAPPING_TRANSFORMATION_KEY = "mapping_transformation"
LEGACY_MAPPING_TRANSFORMATION_KEY = "transformation_rules"


def get_mapping_transformation(migration_rules: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not migration_rules or not isinstance(migration_rules, dict):
        return None
    mt = migration_rules.get(MAPPING_TRANSFORMATION_KEY)
    if isinstance(mt, dict) and mt:
        return mt
    legacy = migration_rules.get(LEGACY_MAPPING_TRANSFORMATION_KEY)
    if isinstance(legacy, dict) and legacy:
        return legacy
    return None


def _dedupe_extraction_transformation_fields(extraction_rules: Dict[str, Any]) -> None:
    """Collapse duplicate merge/split rules (e.g. name vs Name) before persisting a flow."""
    tr = extraction_rules.get("transformation_rules")
    if not isinstance(tr, dict):
        return
    fields = tr.get("fields")
    if not isinstance(fields, list) or len(fields) < 2:
        return
    from utils.rule_merge import merge_transformation_rules

    tr["fields"] = merge_transformation_rules([], fields)


def normalize_migration_rules_for_save(
    migration_rules: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Persist mapping transforms under ``mapping_transformation``; drop legacy top-level key."""
    if not migration_rules or not isinstance(migration_rules, dict):
        return migration_rules
    out = copy.deepcopy(migration_rules)
    legacy = out.pop(LEGACY_MAPPING_TRANSFORMATION_KEY, None)
    if MAPPING_TRANSFORMATION_KEY not in out and isinstance(legacy, dict) and legacy:
        out[MAPPING_TRANSFORMATION_KEY] = legacy
    ex = out.get("extraction_rules")
    if isinstance(ex, dict):
        _dedupe_extraction_transformation_fields(ex)
    return out


def dual_publish_mapping_transform_api(
    payload: Dict[str, Any],
    transformation_rules: Optional[Dict[str, Any]],
) -> None:
    """
    Map-fields / mapping analysis responses: canonical ``mapping_transformation`` plus
    legacy ``transformation_rules`` alias for older clients.
    """
    if not isinstance(payload, dict):
        return
    tr = transformation_rules if isinstance(transformation_rules, dict) else {"fields": []}
    payload[MAPPING_TRANSFORMATION_KEY] = tr
    payload[LEGACY_MAPPING_TRANSFORMATION_KEY] = tr
