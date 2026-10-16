"""Apply user preference for missing required source fields before migration (backend-pull)."""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Set, Tuple

from utils.extraction_record_hidden_keys import is_hidden_extraction_metadata_key

logger = logging.getLogger(__name__)


def _is_missing_value(field_value: Any) -> bool:
    if field_value is None:
        return True
    if field_value == "":
        return True
    if isinstance(field_value, str) and field_value.strip() == "":
        return True
    # Legacy: treat literal Unknown as missing so placeholder can replace
    if field_value == "Unknown":
        return True
    return False


def _mapping_has_required(mapping_info: Any) -> bool:
    if isinstance(mapping_info, list):
        return any(
            isinstance(item, dict) and item.get("is_required", False) for item in mapping_info
        )
    if isinstance(mapping_info, dict):
        return bool(mapping_info.get("is_required", False))
    return False


def _mapping_applies_for_required_skip(
    field_name: str,
    mapping_info: Any,
    source_keys: Set[str],
) -> bool:
    """
    Only enforce skip/placeholder on real source columns for the primary object.

    - Ignore rule/metadata keys on rows.
    - Ignore synthetic entries from mapping refinement (missing=true or SF API name key).
    - Ignore lookup supplemental fields (lookup_of): empty Phone on Account must not
      block Contact migration when Account Name is present.
    """
    if not field_name or is_hidden_extraction_metadata_key(field_name):
        return False
    if field_name not in source_keys:
        return False

    if isinstance(mapping_info, dict):
        if mapping_info.get("missing"):
            return False
        if mapping_info.get("lookup_of"):
            return False
        return bool(mapping_info.get("is_required", False))

    if isinstance(mapping_info, list):
        for item in mapping_info:
            if not isinstance(item, dict):
                continue
            if item.get("missing") or item.get("lookup_of"):
                continue
            if item.get("is_required", False):
                return True
        return False

    return False


def _record_missing_required_for_field(
    record: Dict[str, Any], field_name: str, mapping_info: Any
) -> bool:
    """Match InlineMappingPanel for primary-object required fields on source columns."""
    if isinstance(mapping_info, list):
        return any(
            item.get("is_required", False)
            and not item.get("lookup_of")
            and not item.get("missing")
            and _is_missing_value(record.get(field_name))
            for item in mapping_info
            if isinstance(item, dict)
        )
    if isinstance(mapping_info, dict) and mapping_info.get("is_required", False):
        if mapping_info.get("lookup_of") or mapping_info.get("missing"):
            return False
        return _is_missing_value(record.get(field_name))
    return False


def _source_keys_from_records(data: List[Dict[str, Any]]) -> Set[str]:
    keys: Set[str] = set()
    for row in data[:5]:
        if isinstance(row, dict):
            keys.update(k for k in row.keys() if not is_hidden_extraction_metadata_key(k))
    return keys


def apply_missing_field_preference(
    data: List[Dict[str, Any]],
    mapping: Dict[str, Any],
    migration_rules: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    Filter (skip) or fill placeholder for required mapped fields.

    migration_rules may contain:
        missing_field_preference: {
            "option": "placeholder" | "skip",
            "placeholderValue": str  (when option is "placeholder")
        }

    Returns (processed_rows, skipped_count). For placeholder, skipped_count is 0.
    """
    if not migration_rules or not migration_rules.get("missing_field_preference"):
        return data, 0

    preference = migration_rules["missing_field_preference"]
    option = preference.get("option")
    placeholder_value = preference.get("placeholderValue", "Unknown")

    source_keys = _source_keys_from_records(data)

    if option == "skip":
        processed_data: List[Dict[str, Any]] = []
        skipped_count = 0
        skip_reasons: Counter[str] = Counter()
        for record in data:
            has_missing_required = False
            for field_name, mapping_info in mapping.items():
                if not _mapping_applies_for_required_skip(field_name, mapping_info, source_keys):
                    continue
                if _record_missing_required_for_field(record, field_name, mapping_info):
                    has_missing_required = True
                    skip_reasons[field_name] += 1
                    break
            if not has_missing_required:
                processed_data.append(record)
            else:
                skipped_count += 1
        if skipped_count and skip_reasons:
            top = skip_reasons.most_common(5)
            logger.info(
                "Missing-field skip: omitted %s rows; top source fields: %s",
                skipped_count,
                ", ".join(f"{f} ({n})" for f, n in top),
            )
            print(
                f"📋 Missing-field skip breakdown (top fields): "
                f"{', '.join(f'{f}={n}' for f, n in top)}"
            )
        return processed_data, skipped_count

    if option == "placeholder":
        processed_data = []
        for record in data:
            processed_record = dict(record)
            for field_name, mapping_info in mapping.items():
                if not _mapping_applies_for_required_skip(field_name, mapping_info, source_keys):
                    continue
                if _record_missing_required_for_field(processed_record, field_name, mapping_info):
                    processed_record[field_name] = placeholder_value
            processed_data.append(processed_record)
        return processed_data, 0

    return data, 0
