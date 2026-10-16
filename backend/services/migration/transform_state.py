"""Idempotency guards for mapping-stage transforms at migrate time (all connectors)."""
from __future__ import annotations

from typing import Any, Dict, Optional


def has_mapping_transform_rules(transformation_rules: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(transformation_rules, dict):
        return False
    fields = transformation_rules.get("fields")
    return isinstance(fields, list) and len(fields) > 0


def should_apply_runtime_migration_transform(
    *,
    is_transformed_data: bool,
    transformation_rules: Optional[Dict[str, Any]],
) -> bool:
    """
    True when migrate should run transform_data_for_migration on extraction rows.

    Skips when rows already went through a persisted mapping-transform job
    (is_transformed_data) — same contract as preview /transform-data path.
    """
    if is_transformed_data:
        return False
    return has_mapping_transform_rules(transformation_rules)


def should_apply_mapping_config_transform(
    *,
    is_transformed_data: bool,
    mapping_config_transform_applied: bool = False,
    transformation_rules: Optional[Dict[str, Any]],
) -> bool:
    """True when apply_mapping_transformation_to_config should run at migrate start."""
    if is_transformed_data or mapping_config_transform_applied:
        return False
    return has_mapping_transform_rules(transformation_rules)
