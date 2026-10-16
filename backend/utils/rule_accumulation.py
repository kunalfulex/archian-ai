"""
Rule Accumulation Utilities

Handles accumulation of extraction and cleanup rules with proper field tracking
and dependency resolution for file flows.
"""

from typing import List, Dict, Any, Optional, Set
from datetime import datetime
import copy
import logging
import uuid

from utils.extraction_rule_pipeline import normalize_transformation_rule
from utils.extraction_rules_validate import resolve_field_in_schema

logger = logging.getLogger(__name__)


def _merge_source_fields_from_rule(rule: Dict[str, Any]) -> List[str]:
    """Source field names for merge/combine rules (supports source_field list)."""
    srcs = rule.get("source_fields")
    if srcs is None:
        srcs = rule.get("source_field") or []
    if isinstance(srcs, str):
        return [srcs] if srcs.strip() else []
    if isinstance(srcs, list):
        return [str(s).strip() for s in srcs if str(s).strip()]
    return []


class FieldTracker:
    """
    Tracks which fields are available at each stage:
    - Source fields (from source_schema)
    - Created fields (from transformation rules)
    - Current state (what fields exist now)
    """
    
    def __init__(self, source_schema: List[str]):
        self.source_fields = set(source_schema) if source_schema else set()
        self.created_fields: Set[str] = set()  # Fields created by transformations
        self.available_fields = set(source_schema) if source_schema else set()  # Current available fields
        
    def add_transformation(self, rule: Dict[str, Any]) -> bool:
        """
        Record a transformation and update available fields.
        Returns True if the transformation was applied, False if it was skipped (field not available).
        
        Handles the case where transformations have already been applied to the data:
        - If source_field doesn't exist but output_field does, the transformation was already applied
        - In this case, we update the field tracker state to reflect the current state
        """
        source_field = rule.get("source_field")
        action = rule.get("action", "").lower()
        output_field = rule.get("output_field")
        
        # Check if transformation has already been applied (source_field doesn't exist but output_field does)
        # This happens during cleanup when we're simulating existing transformations on already-transformed data
        if source_field and source_field not in self.available_fields:
            # Check if output_field exists (transformation already applied)
            if output_field and output_field in self.available_fields:
                # Transformation already applied - source_field was renamed/transformed to output_field
                # Update state to reflect this: output_field is a transformed field
                if output_field not in self.source_fields:
                    self.created_fields.add(output_field)
                # For "keep" actions, the field name changed but field still exists
                # For other actions, handle accordingly
                if action == "keep":
                    # Field was renamed - output_field is the current field name
                    # State is already correct (output_field is in available_fields)
                    return True
                elif action in ["split", "split_2", "split_3"]:
                    # Split already applied - output_fields are the current fields
                    output_fields = rule.get("output_fields", [])
                    for of in output_fields:
                        if of not in self.source_fields:
                            self.created_fields.add(of)
                    return True
                elif action == "merge":
                    # Merge already applied - output_field is the current field
                    if output_field not in self.source_fields:
                        self.created_fields.add(output_field)
                    return True
                # For other actions, assume transformation was applied
                return True
            # Source field doesn't exist and output_field doesn't exist either
            return False  # Skip this transformation
        
        # Update available fields based on action
        if action in ["split", "split_2", "split_3"]:
            # Split creates new fields
            target_fields = rule.get("target_fields") or rule.get("output_fields", [])
            if target_fields:
                new_fields = set(target_fields) if isinstance(target_fields, list) else {target_fields}
                self.created_fields.update(new_fields)
                self.available_fields.update(new_fields)
                # Source field is typically removed in split operations
                if source_field in self.available_fields:
                    self.available_fields.discard(source_field)
                    
        elif action == "combine" or action == "merge":
            # Combine consumes source fields, creates new field
            source_fields = _merge_source_fields_from_rule(rule)
            target_field = rule.get("target_field") or rule.get("output_field")
            
            if target_field:
                # Remove consumed source fields
                for sf in source_fields:
                    self.available_fields.discard(sf)
                # Add new combined field
                self.created_fields.add(target_field)
                self.available_fields.add(target_field)
        
        elif action == "exclude":
            bulk = rule.get("fields")
            if isinstance(bulk, list) and bulk:
                for fname in bulk:
                    if not isinstance(fname, str) or not fname.strip():
                        continue
                    tgt = fname.strip().lower()
                    for af in list(self.available_fields):
                        if af.lower() == tgt:
                            self.available_fields.discard(af)
                            break
            elif source_field in self.available_fields:
                self.available_fields.discard(source_field)
        elif action in ("prepend", "append"):
            if source_field:
                for af in list(self.available_fields):
                    if af.lower() == str(source_field).lower():
                        if output_field and str(output_field).lower() != str(af).lower():
                            self.available_fields.discard(af)
                            self.available_fields.add(output_field)
                            if output_field not in self.source_fields:
                                self.created_fields.add(output_field)
                        break
        elif action == "regex_replace":
            if source_field:
                for af in list(self.available_fields):
                    if af.lower() == str(source_field).lower():
                        if output_field and str(output_field).lower() != str(af).lower():
                            self.available_fields.discard(af)
                            self.available_fields.add(output_field)
                            if output_field not in self.source_fields:
                                self.created_fields.add(output_field)
                        break
        elif action == "keep_only":
            cols = rule.get("fields") or []
            allowed = {
                str(c).strip().lower()
                for c in cols
                if isinstance(c, str) and str(c).strip()
            }
            if allowed:
                self.available_fields = {
                    f for f in self.available_fields if str(f).strip().lower() in allowed
                }
        elif action == "keep":
            # Keep action: field is kept, optionally renamed
            # If output_field is different from source_field, it's a rename
            if output_field and output_field != source_field:
                # Field was renamed - remove source_field, add output_field
                if source_field in self.available_fields:
                    self.available_fields.discard(source_field)
                self.available_fields.add(output_field)
                # Mark output_field as created if it's not in source_fields
                if output_field not in self.source_fields:
                    self.created_fields.add(output_field)
            # If output_field is same as source_field or not provided, field stays as-is
        # For other actions (trim, uppercase, normalize, etc.), 
        # field still exists and remains available
        
        return True  # Transformation was successfully applied
    
    def get_available_fields(self) -> Set[str]:
        """Get current available fields"""
        return self.available_fields.copy()
    
    def is_source_field(self, field: str) -> bool:
        """Check if field is from original source"""
        return field in self.source_fields
    
    def is_transformed_field(self, field: str) -> bool:
        """Check if field was created by transformation"""
        return field in self.created_fields


def validate_cleanup_rule(new_rule: Dict[str, Any], field_tracker: FieldTracker) -> bool:
    """
    Validate that cleanup rule can be applied.
    
    Rules can target:
    1. Original source fields (if they still exist)
    2. Transformed fields (created by previous rules)
    
    Raises ValueError if rule cannot be applied.
    """
    action = (new_rule.get("action") or "").strip().lower()
    if action in ("merge", "combine"):
        srcs = _merge_source_fields_from_rule(new_rule)
        if not srcs:
            return True
        missing = [s for s in srcs if s not in field_tracker.get_available_fields()]
        if missing and not any(s in field_tracker.get_available_fields() for s in srcs):
            raise ValueError(
                f"Merge rule references fields {missing} not in available fields: "
                f"{field_tracker.get_available_fields()}"
            )
        return True

    source_field = new_rule.get("source_field")

    if not source_field:
        return True  # Some rules don't have source_field (global rules)

    # Membership against a set() hashes the left operand; lists/dicts raise TypeError here.
    if not isinstance(source_field, str):
        logger.warning(
            "[RULE ACCUMULATION] validate_cleanup_rule: source_field must be str (got %s). "
            "action=%r rule_id=%r keys=%s value=%r",
            type(source_field).__name__,
            new_rule.get("action"),
            new_rule.get("rule_id"),
            sorted(new_rule.keys()),
            source_field,
        )
        raise ValueError(
            f"Cleanup rule source_field must be a string field name, got {type(source_field).__name__}: {source_field!r}. "
            f"Merge-style rules should use source_fields (list) plus target_field/output_field, not source_field as a list."
        )

    if source_field not in field_tracker.get_available_fields():
        # Field doesn't exist - rule cannot be applied
        raise ValueError(
            f"Cleanup rule targets field '{source_field}' which doesn't exist. "
            f"Available fields: {field_tracker.get_available_fields()}. "
            f"Source fields: {field_tracker.source_fields}. "
            f"Transformed fields: {field_tracker.created_fields}"
        )
    
    return True


def accumulate_cleanup_rules(
    existing_rules: Dict[str, Any],
    new_cleanup_rules: Dict[str, Any],
    source_schema: List[str]
) -> Dict[str, Any]:
    """
    Combine new cleanup rules with existing rules.
    
    Strategy:
    1. Initialize field tracker with source schema and existing transformation rules
    2. Validate new rules against current field state
    3. Append transformation rules (preserve order)
    4. Merge data_cleanup_rules (field-level merge - both rules apply)
    5. Add metadata (rule_id, timestamp, operation_type, order)
    
    Args:
        existing_rules: Existing extraction_rules from flow (if any)
        new_cleanup_rules: New cleanup rules to accumulate
        source_schema: Original source schema (field names from source)
    
    Returns:
        Combined extraction_rules with accumulated rules
    """
    # Debug: Log input type for troubleshooting
    if existing_rules is not None and not isinstance(existing_rules, dict):
        print(f"⚠️ [RULE ACCUMULATION] Received existing_rules as {type(existing_rules)}: {str(existing_rules)[:100]}")
    
    # Normalize existing_rules structure - handle different input formats
    if existing_rules is None:
        existing_rules = {}
    elif isinstance(existing_rules, list):
        # If it's a list (transformation_rules["fields"]), wrap it properly
        print(f"🔧 [RULE ACCUMULATION] Normalizing list to dict structure")
        existing_rules = {"transformation_rules": {"fields": existing_rules}}
    elif isinstance(existing_rules, dict):
        # Check if it's a transformation_rules dict (has "fields" but not "transformation_rules")
        if "fields" in existing_rules and "transformation_rules" not in existing_rules and "filter_rules" not in existing_rules:
            # It's a transformation_rules dict, wrap it
            print(f"🔧 [RULE ACCUMULATION] Normalizing transformation_rules dict to full extraction_rules structure")
            existing_rules = {"transformation_rules": existing_rules}
        # If it's already the full extraction_rules structure, use as-is
    else:
        # Unknown type - default to empty dict
        print(f"⚠️ [RULE ACCUMULATION] Unknown existing_rules type: {type(existing_rules)}, using empty dict")
        existing_rules = {}
    
    # Final safety check - ensure existing_rules is a dict
    if not isinstance(existing_rules, dict):
        print(f"❌ [RULE ACCUMULATION] CRITICAL: existing_rules is still not a dict after normalization: {type(existing_rules)}, forcing to empty dict")
        existing_rules = {}
    
    # Initialize field tracker
    field_tracker = FieldTracker(source_schema)
    
    # Build field tracker state from existing transformation rules
    transformation_rules_dict = existing_rules.get("transformation_rules", {})
    if not isinstance(transformation_rules_dict, dict):
        transformation_rules_dict = {}
    existing_transformation_rules = transformation_rules_dict.get("fields", [])
    if not isinstance(existing_transformation_rules, list):
        existing_transformation_rules = []
    
    # Simulate existing transformations to build field tracker state
    # Skip rules that can't be applied (e.g., field already transformed)
    for existing_rule in existing_transformation_rules:
        try:
            applied = field_tracker.add_transformation(existing_rule)
            if not applied:
                print(f"⚠️ [RULE ACCUMULATION] Skipping existing transformation rule for field '{existing_rule.get('source_field')}' - field not available in current state")
        except Exception as e:
            print(f"⚠️ [RULE ACCUMULATION] Error simulating existing transformation rule: {e}. Skipping rule.")
            continue
    
    # Get existing transformation rules
    max_order = max([r.get("order", -1) for r in existing_transformation_rules], default=-1)
    
    # Build a set of existing output_fields to check for duplicates
    # This prevents adding duplicate rules that target the same output_field
    existing_output_fields = set()
    for existing_rule in existing_transformation_rules:
        action = (existing_rule.get("action") or "").lower()
        if action == "keep":
            # For keep actions, check output_field
            output_field = existing_rule.get("output_field")
            if output_field:
                existing_output_fields.add(output_field.lower())
        elif action in ["split", "split_2", "split_3"]:
            # For split actions, check all output_fields
            output_fields = existing_rule.get("output_fields", [])
            if isinstance(output_fields, list):
                for of in output_fields:
                    if of:
                        existing_output_fields.add(of.lower())
        elif action == "merge":
            # For merge actions, check output_field
            output_field = existing_rule.get("output_field")
            if output_field:
                existing_output_fields.add(output_field.lower())
        # For exclude actions, we don't track output_field (they remove fields, not create them)
    
    # Validate and add new transformation rules
    new_transformation_rules = []
    new_transformation_rules_data = new_cleanup_rules.get("transformation_rules", {})
    if isinstance(new_transformation_rules_data, dict):
        new_transformation_rules_list = new_transformation_rules_data.get("fields", [])
    elif isinstance(new_transformation_rules_data, list):
        new_transformation_rules_list = new_transformation_rules_data
    else:
        new_transformation_rules_list = []
    
    for new_rule in new_transformation_rules_list:
        new_rule = normalize_transformation_rule(new_rule)
        # Validate against current field state - skip if field doesn't exist
        try:
            validate_cleanup_rule(new_rule, field_tracker)
        except ValueError as e:
            print(f"⚠️ [RULE ACCUMULATION] Skipping new cleanup transformation rule: {e}")
            continue
        
        # Check for duplicates based on output_field
        action = (new_rule.get("action") or "").lower()
        is_duplicate = False
        
        if action == "keep":
            # For keep actions, check if output_field already exists
            output_field = new_rule.get("output_field")
            if output_field and output_field.lower() in existing_output_fields:
                # Duplicate found - skip this rule
                print(f"⚠️ [RULE ACCUMULATION] Skipping duplicate keep rule for output_field '{output_field}' - already exists in existing rules")
                is_duplicate = True
        elif action in ["split", "split_2", "split_3"]:
            # For split actions, check if any output_fields already exist
            output_fields = new_rule.get("output_fields", [])
            if isinstance(output_fields, list):
                for of in output_fields:
                    if of and of.lower() in existing_output_fields:
                        # Duplicate found - skip this rule
                        print(f"⚠️ [RULE ACCUMULATION] Skipping duplicate split rule - output_field '{of}' already exists in existing rules")
                        is_duplicate = True
                        break
        elif action == "merge":
            # For merge actions, check if output_field already exists
            output_field = new_rule.get("output_field")
            if output_field and output_field.lower() in existing_output_fields:
                # Duplicate found - skip this rule
                print(f"⚠️ [RULE ACCUMULATION] Skipping duplicate merge rule for output_field '{output_field}' - already exists in existing rules")
                is_duplicate = True
        # For exclude actions, we allow duplicates (they may target different fields or be intentional)
        
        if is_duplicate:
            continue
        
        # Add metadata
        new_rule_with_metadata = dict(new_rule)  # Copy to avoid modifying original
        new_rule_with_metadata["rule_id"] = f"rule_{uuid.uuid4().hex[:8]}"
        new_rule_with_metadata["operation_type"] = "cleanup"
        new_rule_with_metadata["timestamp"] = datetime.utcnow().isoformat()
        new_rule_with_metadata["order"] = max_order + 1
        max_order += 1
        
        new_transformation_rules.append(new_rule_with_metadata)
        
        # Update existing_output_fields to track newly added rules
        if action == "keep":
            output_field = new_rule_with_metadata.get("output_field")
            if output_field:
                existing_output_fields.add(output_field.lower())
        elif action in ["split", "split_2", "split_3"]:
            output_fields = new_rule_with_metadata.get("output_fields", [])
            if isinstance(output_fields, list):
                for of in output_fields:
                    if of:
                        existing_output_fields.add(of.lower())
        elif action == "merge":
            output_field = new_rule_with_metadata.get("output_field")
            if output_field:
                existing_output_fields.add(output_field.lower())
        
        # Update field tracker (to validate next rule)
        # If this fails, it means the field was already transformed - that's okay, we still keep the rule
        applied = field_tracker.add_transformation(new_rule_with_metadata)
        if not applied:
            print(f"⚠️ [RULE ACCUMULATION] Could not apply transformation rule to field tracker (field not available). Rule will still be included.")
    
    from utils.rule_merge import merge_transformation_rules

    combined_transformation_rules = merge_transformation_rules(
        existing_transformation_rules, new_transformation_rules
    )
    
    # Merge data_cleanup_rules (field-level)
    existing_data_cleanup = existing_rules.get("data_cleanup_rules", {})
    new_data_cleanup = new_cleanup_rules.get("data_cleanup_rules", {})
    
    # For each field in new cleanup rules, merge with existing
    combined_data_cleanup = {**existing_data_cleanup}
    for field, cleanup_config in new_data_cleanup.items():
        if field in combined_data_cleanup:
            # Field already has cleanup rules - merge them (both apply)
            existing_cleanup = combined_data_cleanup[field]
            if isinstance(existing_cleanup, dict) and isinstance(cleanup_config, dict):
                # Merge cleanup operations (both apply)
                combined_data_cleanup[field] = {
                    **existing_cleanup,
                    **cleanup_config,
                    "last_applied_at": datetime.utcnow().isoformat()
                }
            else:
                # Replace if not dict (fallback)
                combined_data_cleanup[field] = cleanup_config
        else:
            # New field - add cleanup rule
            if isinstance(cleanup_config, dict):
                cleanup_config["applied_at"] = datetime.utcnow().isoformat()
                cleanup_config["operation_type"] = "cleanup"
            combined_data_cleanup[field] = cleanup_config
    
    from utils.rule_merge import merge_deduplication_rules, merge_filter_rules

    ex_filter = existing_rules.get("filter_rules")
    new_filter = new_cleanup_rules.get("filter_rules")
    combined_filter = (
        merge_filter_rules(ex_filter, new_filter)
        if new_filter
        else ex_filter
    )

    combined_dedupe = merge_deduplication_rules(
        existing_rules.get("deduplication_rules"),
        new_cleanup_rules.get("deduplication_rules"),
    )

    combined_rules = {
        "filter_rules": combined_filter,
        "deduplication_rules": combined_dedupe,
        "data_cleanup_rules": combined_data_cleanup,
        "transformation_rules": {
            "fields": combined_transformation_rules
        }
    }
    
    # Preserve other fields from existing rules
    for key in existing_rules:
        if key not in ["filter_rules", "deduplication_rules", "data_cleanup_rules", "transformation_rules"]:
            combined_rules[key] = existing_rules[key]
    
    return combined_rules


def add_metadata_to_transformation_rules(
    transformation_rules: Dict[str, Any],
    operation_type: str = "extraction"
) -> Dict[str, Any]:
    """
    Add metadata (rule_id, timestamp, operation_type, order) to transformation rules.
    
    Used when saving rules for the first time (during initial extraction).
    
    Args:
        transformation_rules: Transformation rules dict with "fields" list
        operation_type: Type of operation ("extraction" or "cleanup")
    
    Returns:
        Transformation rules with metadata added
    """
    if not transformation_rules or not isinstance(transformation_rules, dict):
        return transformation_rules
    
    fields = transformation_rules.get("fields", [])
    if not fields:
        return transformation_rules
    
    fields_with_metadata = []
    for idx, rule in enumerate(fields):
        rule_with_metadata = dict(rule)  # Copy to avoid modifying original
        if "rule_id" not in rule_with_metadata:
            rule_with_metadata["rule_id"] = f"rule_{uuid.uuid4().hex[:8]}"
        if "operation_type" not in rule_with_metadata:
            rule_with_metadata["operation_type"] = operation_type
        if "timestamp" not in rule_with_metadata:
            rule_with_metadata["timestamp"] = datetime.utcnow().isoformat()
        if "order" not in rule_with_metadata:
            rule_with_metadata["order"] = idx
        
        fields_with_metadata.append(rule_with_metadata)
    
    return {
        **transformation_rules,
        "fields": fields_with_metadata
    }


def apply_transformation_rules_in_order(
    data: List[Dict[str, Any]],
    transformation_rules: Dict[str, Any],
    source_schema: List[str]
) -> List[Dict[str, Any]]:
    """
    Apply transformation rules in correct dependency order.
    
    Strategy:
    1. Sort rules by order (or resolve dependencies)
    2. Apply each rule sequentially
    3. Track field state to validate
    
    Args:
        data: Source data (original schema)
        transformation_rules: Transformation rules dict with "fields" list
        source_schema: Original source schema (field names from source)
    
    Returns:
        Transformed data
    """
    if not data or not transformation_rules:
        return data
    
    fields = transformation_rules.get("fields", [])
    if not fields:
        return data
    
    # Sort rules by order
    sorted_rules = sorted(fields, key=lambda r: r.get("order", 0))
    
    # Initialize field tracker
    field_tracker = FieldTracker(source_schema)
    
    current_data = data
    
    # Apply rules in order
    from handlers.file_handler import apply_transformations
    
    for rule in sorted_rules:
        rule = copy.deepcopy(rule)
        action = (rule.get("action") or "").lower()
        schema_list = list(field_tracker.get_available_fields())

        if action in ("merge", "combine"):
            srcs = _merge_source_fields_from_rule(rule)
            resolved = [resolve_field_in_schema(s, schema_list) for s in srcs]
            resolved = [r for r in resolved if r]
            if srcs and not resolved:
                print(
                    f"⚠️ Skipping transformation rule {rule.get('rule_id')}: "
                    f"merge sources {srcs!r} not in schema. "
                    f"Available: {field_tracker.get_available_fields()}"
                )
                continue
            if resolved:
                rule["source_fields"] = resolved
                rule.pop("source_field", None)
        else:
            source_field = rule.get("source_field") or rule.get("field")
            if source_field and isinstance(source_field, str):
                resolved_sf = resolve_field_in_schema(source_field.strip(), schema_list)
                if not resolved_sf:
                    print(
                        f"⚠️ Skipping transformation rule {rule.get('rule_id')}: "
                        f"field '{source_field}' not available. "
                        f"Available: {field_tracker.get_available_fields()}"
                    )
                    continue
                rule["source_field"] = resolved_sf

        # Apply rule using existing transformation logic
        rule_dict = {"fields": [rule]}
        current_data = apply_transformations(current_data, rule_dict)
        
        # Update field tracker
        field_tracker.add_transformation(rule)
    
    return current_data


def extract_source_schema_from_data(data: List[Dict[str, Any]]) -> List[str]:
    """
    Extract source schema (field names) from data.
    
    Args:
        data: List of data records
    
    Returns:
        List of field names (schema)
    """
    if not data or len(data) == 0:
        return []
    
    # Get field names from first record
    first_record = data[0]
    return list(first_record.keys())

