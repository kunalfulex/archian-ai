"""
Extraction Utilities - Shared functions for processing extracted data
Used by file extraction, Salesforce extraction, and SQL extraction
"""

from typing import List, Dict, Any, Optional, Union

from handlers.file_handler import apply_transformations
from utils.extraction_profile import (
    ExtractionProfile,
    resolve_extraction_profile,
    sanitize_extraction_rules_for_profile,
)
from utils.extraction_rule_pipeline import (
    apply_extraction_rules,
    extraction_rules_from_ai_record,
)


def _source_schema_from_records(records: List[Dict[str, Any]]) -> List[str]:
    keys = set()
    for r in records[:50]:
        if isinstance(r, dict):
            keys.update(k for k in r.keys() if isinstance(k, str))
    return sorted(keys)


def apply_extraction_rules_to_structured_data(
    real_data: List[Dict[str, Any]],
    extracted_data: List[Dict[str, Any]],
    *,
    extraction_profile: Optional[Union[str, ExtractionProfile]] = None,
    saved_extraction_rules: Optional[Dict[str, Any]] = None,
    prompt: Optional[str] = None,
    template: Optional[str] = None,
    intent: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Apply cleanup, validation, and transformation rules to structured data.
    
    This is a common utility function used by:
    - File extraction (CSV/Excel/PDF tables)
    - Salesforce extraction
    - SQL extraction
    
    Args:
        real_data: List of actual data records to process
        extracted_data: List of records from AI extraction (contains rules in first record)
        
    Returns:
        List of processed records with transformations applied and metadata removed
    """
    if not extracted_data or len(extracted_data) == 0:
        return real_data
    
    # Extract transformation_rules from AI output (first record)
    transformation_rules = None
    if extracted_data and len(extracted_data) > 0:
        raw_transformation_rules = extracted_data[0].get("transformation_rules")
        # Handle case where transformation_rules might be a list (incorrect format) or dict (correct format)
        if isinstance(raw_transformation_rules, dict):
            transformation_rules = raw_transformation_rules
        elif isinstance(raw_transformation_rules, list):
            # If it's a list, wrap it in the expected dict format
            transformation_rules = {"fields": raw_transformation_rules}
        # If None or other type, leave as None
    
    meta = extracted_data[0] if isinstance(extracted_data[0], dict) else {}
    rules_from_ai = extraction_rules_from_ai_record(meta)
    if transformation_rules:
        rules_from_ai["transformation_rules"] = transformation_rules

    if isinstance(extraction_profile, str):
        try:
            profile = ExtractionProfile(extraction_profile.strip().lower())
        except ValueError:
            profile = resolve_extraction_profile(
                saved_extraction_rules=saved_extraction_rules,
                prompt=prompt,
                template=template,
                intent=intent,
                source=source,
            )
    elif isinstance(extraction_profile, ExtractionProfile):
        profile = extraction_profile
    else:
        profile = resolve_extraction_profile(
            saved_extraction_rules=saved_extraction_rules or rules_from_ai,
            prompt=prompt,
            template=template,
            intent=intent,
            source=source,
        )

    source_schema = _source_schema_from_records(real_data)
    if rules_from_ai:
        rules_from_ai = sanitize_extraction_rules_for_profile(
            rules_from_ai, profile, source_schema=source_schema
        )

    if profile == ExtractionProfile.SOURCE_TRUE_MIGRATE:
        processed_data = list(real_data)
    else:
        processed_data = apply_extraction_rules(
            real_data, rules_from_ai or None, source_schema=source_schema
        )

    transformation_rules = rules_from_ai.get("transformation_rules") if rules_from_ai else transformation_rules
    if transformation_rules and isinstance(transformation_rules, dict) and transformation_rules.get("fields"):
        openai_schema_fields = []
        for rule in transformation_rules["fields"]:
            action = (rule.get("action") or "").lower()
            if action in ["split_2", "split_3", "split"]:
                openai_schema_fields.extend(rule.get("output_fields", []) or [])
            elif action == "keep":
                output_field = rule.get("output_field")
                if not output_field:
                    sf = rule.get("source_field")
                    output_field = sf[0] if isinstance(sf, list) and sf else sf
                if output_field:
                    openai_schema_fields.append(output_field)
            elif action == "merge":
                if rule.get("output_field"):
                    openai_schema_fields.append(rule["output_field"])
        if openai_schema_fields:
            seen_fields = set()
            deduped_fields = []
            for field in openai_schema_fields:
                if field and field not in seen_fields:
                    seen_fields.add(field)
                    deduped_fields.append(field)
            enriched_data = []
            for record in processed_data:
                enriched_record = dict(record)
                for field in deduped_fields:
                    if field not in enriched_record:
                        enriched_record[field] = record.get(field, "")
                enriched_data.append(enriched_record)
            processed_data = enriched_data
    
    # Step 3: Remove metadata fields from first record if present
    if processed_data and isinstance(processed_data, list) and len(processed_data) > 0:
        if "data_cleanup_rules" in processed_data[0]:
            del processed_data[0]["data_cleanup_rules"]
        if "transformation_rules" in processed_data[0]:
            del processed_data[0]["transformation_rules"]
        if "filter_rules" in processed_data[0]:
            del processed_data[0]["filter_rules"]
        if "deduplication_rules" in processed_data[0]:
            del processed_data[0]["deduplication_rules"]
        if "mapping_context_summary" in processed_data[0]:
            del processed_data[0]["mapping_context_summary"]
    
    return processed_data


def apply_all_extraction_rules(
    data: List[Dict[str, Any]],
    extraction_rules: Dict[str, Any],
    *,
    extraction_profile: Optional[Union[str, ExtractionProfile]] = None,
    prompt: Optional[str] = None,
    template: Optional[str] = None,
    intent: Optional[str] = None,
    source: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Apply all extraction rules to data in the correct order.
    
    This is a unified function that handles all rule types for flow reruns:
    - transformation_rules: Transform field structures (applied first so field names match filter rules)
    - filter_rules: Filter records based on conditions (applied after transformation)
    - deduplication_rules: Remove duplicate records (applied after filtering)
    - data_cleanup_rules: Clean and validate field values (applied after deduplication)
    
    Args:
        data: List of records to process
        extraction_rules: Dict containing all rule types:
            - filter_rules: Optional dict with "conditions" and "logic"
            - deduplication_rules: Optional dict with "enabled", "fields", "strategy"
            - data_cleanup_rules: Optional dict with field-specific cleanup rules
            - transformation_rules: Optional dict with "fields" list
    
    Returns:
        Processed data with all applicable rules applied
    
    Example:
        extraction_rules = {
            "filter_rules": {"conditions": [...], "logic": "AND"},
            "deduplication_rules": {"enabled": True, "fields": ["Email"], "strategy": "keep_first"},
            "data_cleanup_rules": {"email": {"type": "email"}, "phone": {"type": "phone"}},
            "transformation_rules": {"fields": [...]}
        }
        processed_data = apply_all_extraction_rules(data, extraction_rules)
    """
    if not data or not extraction_rules:
        return data

    if isinstance(extraction_profile, ExtractionProfile):
        profile = extraction_profile
    elif extraction_profile:
        try:
            profile = ExtractionProfile(str(extraction_profile).strip().lower())
        except ValueError:
            profile = resolve_extraction_profile(
                saved_extraction_rules=extraction_rules,
                prompt=prompt,
                template=template,
                intent=intent,
                source=source,
            )
    else:
        profile = resolve_extraction_profile(
            saved_extraction_rules=extraction_rules,
            prompt=prompt,
            template=template,
            intent=intent,
            source=source,
        )

    schema = extraction_rules.get("source_schema") if isinstance(extraction_rules, dict) else None
    if not schema:
        schema = _source_schema_from_records(data)
    rules = sanitize_extraction_rules_for_profile(
        extraction_rules, profile, source_schema=schema
    )
    if profile == ExtractionProfile.SOURCE_TRUE_MIGRATE:
        tr = rules.get("transformation_rules") if isinstance(rules, dict) else None
        if tr and tr.get("fields"):
            return apply_extraction_rules(data, rules, source_schema=schema)
        return data
    return apply_extraction_rules(data, rules, source_schema=schema)

