"""
Utility functions for mapping transformations.

This module provides functions to transform field mappings to match
transformed data structures (e.g., flattening split field arrays).
"""

from typing import Any, Dict, List, Optional


# Keys that may appear in mapping dicts during transform but are not field mappings.
_NON_FIELD_MAPPING_KEYS = frozenset(
    {
        "filter_rules",
        "deduplication_rules",
        "transformation_rules",
        "data_cleanup_rules",
        "ordering_rules",
        "source_schema",
        "mapping_context_summary",
    }
)


def sanitize_mapping_for_save(mapping: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Drop rule/metadata keys; keep dict or list values per extracted field."""
    if not isinstance(mapping, dict):
        return {}
    return {
        k: v
        for k, v in mapping.items()
        if k and k not in _NON_FIELD_MAPPING_KEYS and isinstance(v, (dict, list))
    }


def _normalize_field_token(name: str) -> str:
    return (name or "").lower().replace("_", "").replace(" ", "")


def resolve_extracted_field_key(
    field_name: str,
    data_keys: Optional[List[str]],
) -> str:
    """
    Map LLM/snake_case mapping keys to keys present in extraction rows.

    Salesforce extraction uses API names (Email, FirstName); the mapping LLM
    often returns email, first_name, last_name. Bulk migration uses
    record.get(source_field) so keys must match extraction columns.
    """
    if not field_name:
        return field_name or ""
    if not data_keys:
        return field_name
    if field_name in data_keys:
        return field_name
    target = _normalize_field_token(field_name)
    for key in data_keys:
        if _normalize_field_token(key) == target:
            return key
    return field_name


def flat_data_uses_salesforce_extract_keys(flat_data: Optional[Dict[str, Any]]) -> bool:
    """
    True when extract rows use Salesforce API / relationship-flattened keys
    (FirstName, AccountName), not file-upload snake_case (first_name, company).
    """
    if not isinstance(flat_data, dict) or not flat_data:
        return False
    skip = frozenset(
        {
            "filter_rules",
            "deduplication_rules",
            "transformation_rules",
            "data_cleanup_rules",
            "attachments",
            "_attachments",
        }
    )
    keys = [k for k in flat_data if k and k not in skip and not str(k).startswith("_")]
    if not keys:
        return False
    markers = {
        "FirstName",
        "LastName",
        "Email",
        "Id",
        "AccountName",
        "AccountPhone",
        "OwnerId",
    }
    if len(set(keys) & markers) >= 2:
        return True
    pascal = sum(
        1
        for k in keys
        if k[0].isupper() and "_" not in k and k not in skip
    )
    snake = sum(1 for k in keys if "_" in str(k))
    return pascal >= 2 and pascal > snake


# LLM often invents file-style keys; map to Salesforce extract column names.
_LLM_SYNONYM_TO_EXTRACT: Dict[str, str] = {
    "company": "AccountName",
    "accountname": "AccountName",
    "account_name": "AccountName",
    "accountphone": "AccountPhone",
    "account_phone": "AccountPhone",
}


def repair_salesforce_mapping_to_extract_keys(
    mapping: Dict[str, Any],
    flat_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Re-key LLM mapping to match Salesforce extraction columns; fix Phone vs AccountPhone hijack.
    Drops mapping keys that do not correspond to any extracted column.
    """
    if not mapping:
        return {}
    data_keys = []
    if isinstance(flat_data, dict):
        data_keys = [
            k
            for k in flat_data.keys()
            if k and not str(k).startswith("_")
            and str(k).lower() not in ("attachments", "_attachments")
        ]
    data_set = set(data_keys)
    if not data_set:
        return dict(mapping)

    repaired: Dict[str, Any] = {}

    def _attach_value(canon_key: str, spec: Any) -> None:
        if not isinstance(spec, dict):
            return
        out = dict(spec)
        if isinstance(flat_data, dict):
            sample = flat_data.get(canon_key)
            if sample is not None and out.get("value") in (None, "", "[SAMPLE_VALUE]"):
                out["value"] = sample
        repaired[canon_key] = out

    for src_key, spec in mapping.items():
        norm = _normalize_field_token(src_key)
        canon = resolve_extracted_field_key(src_key, data_keys)
        if norm in _LLM_SYNONYM_TO_EXTRACT and _LLM_SYNONYM_TO_EXTRACT[norm] in data_set:
            canon = _LLM_SYNONYM_TO_EXTRACT[norm]
        if canon not in data_set:
            continue
        if canon in repaired and canon != src_key:
            continue
        _attach_value(canon, spec)

    # Phone on Contact vs AccountPhone on Account (common LLM mistake)
    if "Phone" in data_set and "AccountPhone" in data_set:
        phone_spec = repaired.get("Phone")
        if isinstance(phone_spec, dict) and (phone_spec.get("object") or "").lower() == "account":
            acct_spec = dict(phone_spec)
            acct_spec["field"] = "Phone"
            if "AccountPhone" not in repaired:
                repaired["AccountPhone"] = acct_spec
            contact_spec = {
                "object": "Contact",
                "field": "Phone",
                "is_required": phone_spec.get("is_required", False),
                "value": flat_data.get("Phone") if isinstance(flat_data, dict) else None,
                "is_custom": phone_spec.get("is_custom", False),
                "is_duplicate_check": phone_spec.get("is_duplicate_check", False),
            }
            if contact_spec.get("value") not in (None, ""):
                repaired["Phone"] = contact_spec

    if "AccountName" in data_set and "AccountName" not in repaired:
        for alias in ("company", "Company", "account_name", "Account Name"):
            if alias in mapping and isinstance(mapping[alias], dict):
                _attach_value("AccountName", mapping[alias])
                repaired.pop("company", None)
                break

    return repaired


def align_mapping_keys_to_extracted_data(
    mapping: Dict[str, Any],
    flat_data: Optional[Dict[str, Any]] = None,
    *,
    data_keys: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Re-key mapping dict so source keys match extraction record column names."""
    if not mapping:
        return {}
    keys = list(data_keys or [])
    if not keys and isinstance(flat_data, dict):
        keys = [
            k
            for k in flat_data.keys()
            if k and not str(k).startswith("_")
            and str(k).lower() not in ("attachments", "_attachments")
        ]
    if not keys:
        return dict(mapping)

    aligned: Dict[str, Any] = {}
    for source_key, mapping_value in mapping.items():
        actual_key = resolve_extracted_field_key(source_key, keys)
        if actual_key in aligned and actual_key != source_key:
            continue
        aligned[actual_key] = mapping_value
        if isinstance(mapping_value, dict) and isinstance(flat_data, dict):
            sample = flat_data.get(actual_key)
            if sample is not None and mapping_value.get("value") in (None, "", "[SAMPLE_VALUE]"):
                mapping_value = dict(mapping_value)
                mapping_value["value"] = sample
                aligned[actual_key] = mapping_value
    return aligned


def record_value_for_mapping_key(
    record: Dict[str, Any],
    source_field: str,
) -> Any:
    """Read extraction row value using mapping key with case/snake normalization."""
    if not record or not source_field:
        return None
    if source_field in record:
        return record.get(source_field)
    resolved = resolve_extracted_field_key(source_field, list(record.keys()))
    if resolved != source_field:
        return record.get(resolved)
    return None


def transform_mapping_for_migration(
    mapping: Dict[str, Any],
    *,
    use_destination_field_keys: bool = False,
) -> Dict[str, Any]:
    """
    Transform mapping structure to match transformed data keys.
    
    For split fields (arrays in mapping), replaces the array with individual
    keys using destination field names. This ensures mapping keys match the
    transformed data keys after field splitting.
    
    Example:
        Input mapping:
        {
            "Contact Name": [
                {"field": "FirstName", "object": "Contact", ...},
                {"field": "LastName", "object": "Contact", ...}
            ],
            "Leave Date": {"field": "Leave_Date__c", "object": "Leave__c", ...}
        }
        
        Output mapping:
        {
            "FirstName": {"field": "FirstName", "object": "Contact", ...},
            "LastName": {"field": "LastName", "object": "Contact", ...},
            "Leave Date": {"field": "Leave_Date__c", "object": "Leave__c", ...}
        }
    
    Args:
        mapping: Original mapping dictionary that may contain arrays for split fields
        
    Returns:
        Transformed mapping dictionary with split field arrays flattened to individual keys
    """
    transformed_mapping = {}
    
    for source_field_key, mapping_value in mapping.items():
        # Check if this is a split field (array of mapping objects)
        if isinstance(mapping_value, list):
            # For split fields, create individual entries for each array item
            # using the destination field name as the key
            for mapping_item in mapping_value:
                if isinstance(mapping_item, dict):
                    destination_field_name = mapping_item.get("field")
                    if destination_field_name:
                        # Use destination field name as the new key
                        # Copy all properties from the mapping item
                        transformed_mapping[destination_field_name] = dict(mapping_item)
                    else:
                        # Fallback: if no field name, skip this item
                        print(f"⚠️ Warning: Split field mapping item missing 'field' property, skipping")
        elif isinstance(mapping_value, dict):
            # Pre-transformed rows use destination API names as keys (Email, FirstName, …).
            if use_destination_field_keys:
                dest_key = mapping_value.get("field") or mapping_value.get("column")
                if dest_key:
                    transformed_mapping[dest_key] = dict(mapping_value)
                else:
                    transformed_mapping[source_field_key] = mapping_value
            else:
                transformed_mapping[source_field_key] = mapping_value
        else:
            # Unexpected type - keep as-is but log warning
            print(f"⚠️ Warning: Unexpected mapping value type for '{source_field_key}': {type(mapping_value)}")
            transformed_mapping[source_field_key] = mapping_value
    
    return transformed_mapping
