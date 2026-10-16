"""Validate mapping plans before returning to UI."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from services.mapping_plan.models import ParsedMappingInstruction


def validate_mapping_single_primary_object(
    mapping: Dict[str, Any],
    primary_object: str,
) -> List[str]:
    """Warn-level validation; returns list of issue strings."""
    issues: List[str] = []
    if not primary_object:
        return issues
    objects: Set[str] = set()
    for _key, val in (mapping or {}).items():
        if isinstance(val, dict):
            obj = val.get("object")
            if obj:
                objects.add(str(obj))
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict) and item.get("object"):
                    objects.add(str(item["object"]))
    extras = objects - {primary_object}
  # Lookups are ok on other objects
    base_extras = {o for o in extras if o != primary_object}
    if len(base_extras) > 3:
        issues.append(
            f"Mapping spans many objects ({', '.join(sorted(objects))}); "
            f"expected primary target {primary_object}."
        )
    return issues


def validate_parsed_instruction(parsed: ParsedMappingInstruction) -> List[str]:
    issues: List[str] = []
    if parsed.raw and not (
        parsed.target_object_api_name or parsed.target_table or parsed.target_sheet
    ):
        if "object" in parsed.raw.lower() or "table" in parsed.raw.lower():
            issues.append("Could not resolve destination object/table from instruction.")
    return issues
