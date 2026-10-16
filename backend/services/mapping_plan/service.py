"""Backward-compatible re-exports — prefer services.mapping.service."""
from __future__ import annotations

from services.mapping.service import (
    build_mapping_plan_request,
    execute_mapping,
    mapping_plan_result_to_legacy_dict,
    mapping_result_to_legacy_dict,
)

__all__ = [
    "build_mapping_plan_request",
    "execute_mapping",
    "mapping_plan_result_to_legacy_dict",
    "mapping_result_to_legacy_dict",
]
