"""
File Handler - Handles all file extraction and processing logic
Supports CSV, Excel, PDF, TXT, and Image files with AI-powered data extraction
"""

import json
import re
import os
import asyncio
from datetime import datetime
import pandas as pd
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from dateutil import parser as date_parser
from email_validator import EmailNotValidError, validate_email

# Import utilities
from utils.openai_helpers import parse_openai_json_response, fix_malformed_json
from utils.ocr_utils import extract_text_from_image
from db.connection import database

# Load environment variables
load_dotenv(override=True)


# Chunk threshold for large files
CHUNK_THRESHOLD = 100


def _re_sub_replacement_normalize_llm(repl: str) -> str:
    """
    LLMs often emit Perl-style $1, $2 in substitution templates.
    Python's re.sub treats those literally; map to \\g<n> backreferences.
    """
    if not isinstance(repl, str):
        repl = str(repl)
    return re.sub(r"\$(\d+)", lambda m: "\\g<" + m.group(1) + ">", repl)


def _maybe_format_us_phone_e164(value: str) -> str:
    """
    If value is E.164 US (+1 + 10 digits, optional spaces), format as +1 (AAA) BBB-CCCC.
    Otherwise return value unchanged.
    """
    if not isinstance(value, str):
        return str(value) if value is not None else ""
    compact = re.sub(r"\s+", "", value.strip())
    m = re.fullmatch(r"\+1(\d{10})", compact)
    if not m:
        return value
    d = m.group(1)
    return f"+1 ({d[0:3]}) {d[3:6]}-{d[6:10]}"


_PHONE_FIELD_NAME_HINT = re.compile(r"phone|mobile|cell|tel|fax", re.I)


def _field_name_suggests_phone(field_name: str) -> bool:
    """True for Phone, AccountPhone, MobilePhone, etc."""
    return bool(field_name and _PHONE_FIELD_NAME_HINT.search(str(field_name)))


def _strip_erroneous_us_prefix_before_foreign_plus(collapsed: str) -> str:
    """Turn +1+44... into +44... (naive prepend / regex ^ before another country code)."""
    c = collapsed.strip()
    while len(c) >= 4 and c.startswith("+1+"):
        c = c[2:]
    return c


def _affix_targets_us_country_code(affix: str) -> bool:
    a = (affix or "").strip().replace(" ", "")
    return a in ("+1", "1")


def _regex_replace_is_us_country_prepend(pattern: str, replacement: Any) -> bool:
    """Detect regex '^' + '+1' style rules that should use NANP/international semantics."""
    r = str(replacement or "").strip().replace(" ", "")
    if r not in ("+1", "1"):
        return False
    p = (pattern or "").strip()
    return p == "^" or p == r"\A"


def normalize_phone_us_default_region(value: str, *, default_region: str = "US") -> str:
    """
    Phone normalization for \"add US country code\" flows:
    - Values already international (+44...) → keep country; parse to E.164 when possible.
    - NANP-style nationals → +1 E.164 (when parseable or 10-digit fallback).
    - Repairs mistaken +1 stacked before another +country (+1+44...).
    """
    if value is None:
        return ""
    raw_in = str(value).strip()
    if not raw_in:
        return ""

    collapsed = re.sub(r"\s+", "", raw_in)
    collapsed = _strip_erroneous_us_prefix_before_foreign_plus(collapsed)

    try:
        import phonenumbers
        from phonenumbers import NumberParseException, PhoneNumberFormat

        if collapsed.startswith("+"):
            try:
                num = phonenumbers.parse(collapsed, None)
                if phonenumbers.is_possible_number(num):
                    return phonenumbers.format_number(num, PhoneNumberFormat.E164)
            except NumberParseException:
                pass
            return raw_in

        digits_only = re.sub(r"\D", "", collapsed)
        if len(digits_only) == 11 and digits_only.startswith("1"):
            try:
                num = phonenumbers.parse("+" + digits_only, None)
                if phonenumbers.is_possible_number(num):
                    return phonenumbers.format_number(num, PhoneNumberFormat.E164)
            except NumberParseException:
                pass

        if len(digits_only) == 10:
            try:
                num = phonenumbers.parse(digits_only, default_region)
                if phonenumbers.is_possible_number(num):
                    return phonenumbers.format_number(num, PhoneNumberFormat.E164)
            except NumberParseException:
                pass
            return "+1" + digits_only

        try:
            num = phonenumbers.parse(raw_in, default_region)
            if phonenumbers.is_possible_number(num):
                return phonenumbers.format_number(num, PhoneNumberFormat.E164)
        except NumberParseException:
            pass
    except ImportError:
        pass

    # phonenumbers missing or unparsed: minimal safe behavior
    if collapsed.startswith("+") and not collapsed.startswith("+1+"):
        return collapsed
    collapsed = _strip_erroneous_us_prefix_before_foreign_plus(collapsed)
    if collapsed.startswith("+") and not collapsed.startswith("+1"):
        return collapsed
    digits_only = re.sub(r"\D", "", collapsed)
    if len(digits_only) == 10:
        return "+1" + digits_only
    return collapsed if collapsed else raw_in


def _data_ops_regex_limits() -> tuple[int, int]:
    """Max pattern length and max subject length for regex filter / cleanup (ReDoS mitigation)."""
    try:
        max_pat = int(os.getenv("DATA_OPS_REGEX_MAX_PATTERN_LEN", "256"))
    except ValueError:
        max_pat = 256
    try:
        max_sub = int(os.getenv("DATA_OPS_REGEX_MAX_SUBJECT_LEN", "100000"))
    except ValueError:
        max_sub = 100000
    return max(8, min(max_pat, 4096)), max(256, min(max_sub, 2_000_000))


def _parse_date_for_filter(value: Any) -> Any:
    """Return datetime.date for comparison, or None if unparseable."""
    from datetime import date, datetime as dt

    if value is None:
        return None
    if isinstance(value, dt):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return date_parser.parse(s, dayfirst=False, yearfirst=True).date()
    except (ValueError, TypeError, OverflowError, AttributeError):
        return None


def _cleanup_phone_e164(value: str, rules: Dict[str, Any]) -> str:
    try:
        import phonenumbers
        from phonenumbers import NumberParseException, PhoneNumberFormat
    except ImportError:
        return "".join(c for c in value if c.isdigit() or c in "+-")
    region = rules.get("default_region") or "US"
    if not isinstance(region, str):
        region = "US"
    try:
        num = phonenumbers.parse(value.strip(), region)
        if not phonenumbers.is_valid_number(num):
            return value.strip()
        return phonenumbers.format_number(num, PhoneNumberFormat.E164)
    except NumberParseException:
        return value.strip()


def _cleanup_country_alpha2(value: str, rules: Dict[str, Any]) -> str:
    mode = (rules.get("normalize_country") or "").strip().lower()
    if mode != "alpha2":
        return value.strip()
    try:
        import pycountry
    except ImportError:
        return value.strip()
    s = value.strip()
    if len(s) == 2 and s.isalpha():
        return s.upper()
    try:
        return pycountry.countries.lookup(s).alpha_2
    except LookupError:
        return s


def _values_equal_for_filter(record_value: Any, target_value: Any) -> bool:
    """Loose equality for filter `in` / `not_in` (numeric try, else case-insensitive string)."""
    if record_value is None and target_value is None:
        return True
    if record_value is None or target_value is None:
        return False
    try:
        return float(record_value) == float(target_value)
    except (ValueError, TypeError):
        return str(record_value).strip().lower() == str(target_value).strip().lower()


def _filter_membership_in_list(record_value: Any, value_spec: Any, *, negate: bool) -> bool:
    """
    `in` / `not_in` filter: value is a list (or coerced single value).
    `in []` matches nothing; `not_in []` matches any non-missing field (vacuous).
    """
    if isinstance(value_spec, (list, tuple, set)):
        allowed = list(value_spec)
    elif value_spec is None:
        allowed = []
    else:
        allowed = [value_spec]
    if not allowed:
        return False if not negate else True
    member = any(_values_equal_for_filter(record_value, v) for v in allowed)
    return (not member) if negate else member


# ============================================================================
# HELPER FUNCTIONS - Filtering, Transformation, Cleanup
# ============================================================================

def matches_condition(record: dict, condition: dict) -> bool:
    """
    Check if a record matches a single filter condition.

    Missing-field semantics for ``is_empty`` / ``is_not_empty`` vs other operators are documented in
    ``edison-ai/docs/FILTER_OPERATOR_MISSING_FIELD_SEMANTICS.md``.

    Args:
        record: The data record to check
        condition: Filter condition with field, operator, and value

    Returns:
        True if the record matches the condition, False otherwise
    """
    field = condition.get("field")
    operator = condition.get("operator")
    if isinstance(operator, str):
        operator = operator.strip().lower()
    target_value = condition.get("value")

    # Missing key: only is_empty / is_not_empty have explicit semantics (see FILTER_OPERATOR_MISSING_FIELD_SEMANTICS.md)
    if operator == "is_empty":
        if field not in record:
            return True
        record_value = record[field]
        return record_value is None or (isinstance(record_value, str) and record_value.strip() == "")

    if operator == "is_not_empty":
        if field not in record:
            return False
        record_value = record[field]
        return record_value is not None and (not isinstance(record_value, str) or record_value.strip() != "")

    if field not in record:
        return False

    record_value = record[field]

    if operator in ("is_valid", "email_is_valid"):
        # LLMs often emit is_valid + value:true for email checks; map to valid_email semantics
        field_lc = (field or "").lower()
        want_valid = True
        if target_value is False:
            want_valid = False
        elif isinstance(target_value, str) and target_value.strip().lower() in ("false", "0", "no"):
            want_valid = False

        def _row_email_valid() -> bool:
            if record_value is None:
                return False
            s = str(record_value).strip()
            if not s:
                return False
            try:
                validate_email(s, check_deliverability=False)
                return True
            except EmailNotValidError:
                return False

        if "email" in field_lc or field_lc.endswith("e-mail"):
            ok = _row_email_valid()
            return ok if want_valid else not ok
        # Non-email fields: treat as non-empty when "valid"
        nonempty = record_value is not None and (
            not isinstance(record_value, str) or record_value.strip() != ""
        )
        return nonempty if want_valid else not nonempty

    if operator == "valid_email":
        if record_value is None:
            return False
        s = str(record_value).strip()
        if not s:
            return False
        try:
            validate_email(s, check_deliverability=False)
            return True
        except EmailNotValidError:
            return False

    if operator in ("in", "is_in"):
        return _filter_membership_in_list(record_value, target_value, negate=False)

    if operator in ("not_in", "nin", "is_not_in"):
        return _filter_membership_in_list(record_value, target_value, negate=True)

    if operator == "regex_matches":
        max_pat, max_sub = _data_ops_regex_limits()
        if record_value is None:
            return False
        s = str(record_value)
        if len(s) > max_sub:
            return False
        pattern = target_value
        if not isinstance(pattern, str) or not pattern.strip():
            return False
        if len(pattern) > max_pat:
            return False
        flags = re.IGNORECASE if condition.get("case_insensitive") in (True, "true", "1", "yes", "on") else 0
        try:
            return re.search(pattern, s, flags) is not None
        except re.error:
            return False

    if operator == "date_between":
        if not isinstance(target_value, (list, tuple)) or len(target_value) != 2:
            return False
        ds_lo = _parse_date_for_filter(target_value[0])
        ds_hi = _parse_date_for_filter(target_value[1])
        if ds_lo is None or ds_hi is None:
            return False
        if ds_lo > ds_hi:
            ds_lo, ds_hi = ds_hi, ds_lo
        dr = _parse_date_for_filter(record_value)
        if dr is None:
            return False
        return ds_lo <= dr <= ds_hi

    if operator == "date_on_or_after":
        b = _parse_date_for_filter(target_value)
        dr = _parse_date_for_filter(record_value)
        if b is None or dr is None:
            return False
        return dr >= b

    if operator == "date_on_or_before":
        b = _parse_date_for_filter(target_value)
        dr = _parse_date_for_filter(record_value)
        if b is None or dr is None:
            return False
        return dr <= b

    # For other operators, if record value is None/empty, condition fails
    if record_value is None or (isinstance(record_value, str) and record_value.strip() == ""):
        return False
    
    try:
        # String comparison operators
        if operator == "equals":
            # Try numeric comparison first, fall back to string
            try:
                return float(record_value) == float(target_value)
            except (ValueError, TypeError):
                return str(record_value).lower() == str(target_value).lower()
        
        elif operator == "not_equals":
            try:
                return float(record_value) != float(target_value)
            except (ValueError, TypeError):
                return str(record_value).lower() != str(target_value).lower()
        
        elif operator == "contains":
            return str(target_value).lower() in str(record_value).lower()
        
        elif operator == "not_contains":
            return str(target_value).lower() not in str(record_value).lower()
        
        elif operator == "starts_with":
            return str(record_value).lower().startswith(str(target_value).lower())
        
        elif operator == "ends_with":
            return str(record_value).lower().endswith(str(target_value).lower())
        
        # Numeric comparison operators
        elif operator == "greater_than":
            return float(record_value) > float(target_value)
        
        elif operator == "less_than":
            return float(record_value) < float(target_value)
        
        elif operator == "greater_than_or_equal":
            return float(record_value) >= float(target_value)
        
        elif operator == "less_than_or_equal":
            return float(record_value) <= float(target_value)
        
        elif operator == "between":
            # Expects value to be a list [min, max]
            if isinstance(target_value, list) and len(target_value) == 2:
                return float(target_value[0]) <= float(record_value) <= float(target_value[1])
            return False
        
        else:
            from utils.extraction_rules_validate import ALLOWED_FILTER_OPERATORS

            print(f"⚠️ Unknown filter operator: {operator}")
            if operator not in ALLOWED_FILTER_OPERATORS:
                return False
            # Declared allowed but not implemented — fail closed
            return False
            
    except (ValueError, TypeError) as e:
        print(f"⚠️ Error comparing {field}: {record_value} {operator} {target_value} - {e}")
        return False


def apply_filter_rules(data: list, filter_rules: dict) -> list:
    """
    Filter records based on user-specified criteria
    Only returns records that match the filter conditions
    
    Args:
        data: List of records to filter
        filter_rules: Filter rules from AI with conditions and logic
        
    Returns:
        Filtered list of records
    """
    if not filter_rules or not filter_rules.get("conditions"):
        return data
    
    conditions = filter_rules["conditions"]
    logic = filter_rules.get("logic", "AND").upper()
    
    if not conditions:
        return data
    
    filtered_records = []
    
    for record in data:
        if logic == "AND":
            # All conditions must match
            if all(matches_condition(record, cond) for cond in conditions):
                filtered_records.append(record)
        elif logic == "OR":
            # Any condition must match
            if any(matches_condition(record, cond) for cond in conditions):
                filtered_records.append(record)
        else:
            # Unknown logic, default to AND
            if all(matches_condition(record, cond) for cond in conditions):
                filtered_records.append(record)
    
    print(f"📊 Filtering: {len(data)} records → {len(filtered_records)} records after applying filter rules")
    
    return filtered_records


def _sort_key_value(record: dict, field_name: str) -> Any:
    """Resolve field case-insensitively; missing field → None."""
    if not field_name or not isinstance(record, dict):
        return None
    for k, v in record.items():
        if k.lower() == str(field_name).lower():
            return v
    return None


def _ordering_max_limit() -> int:
    try:
        n = int(os.getenv("DATA_OPS_ORDERING_MAX_LIMIT", "500000"))
    except ValueError:
        n = 500000
    return max(1, min(n, 10_000_000))


def _scalar_for_sort(v: Any) -> Any:
    if v is None:
        return (1, "")
    try:
        return (0, float(v))
    except (TypeError, ValueError):
        return (1, str(v).lower())


def apply_ordering_rules(
    data: list,
    ordering_rules: Optional[dict],
    *,
    apply_row_limit: bool = True,
) -> list:
    """
    Stable multi-key sort and optional row limit. Applied after filter in the extraction pipeline.

    ordering_rules shape::
        {
          "sort": [{"field": "LastName", "direction": "asc"}, ...],
          "limit": 100
        }
    Directions: ``asc`` or ``desc`` (case-insensitive). Sort specs are applied **least-significant first**
    (stable sort) so multi-key ordering matches common SQL semantics.

    Set ``apply_row_limit=False`` for post-extract data ops so the full filtered/transformed set is kept.
    """
    if not data or not ordering_rules or not isinstance(ordering_rules, dict):
        return data

    out = list(data)
    sort_specs = ordering_rules.get("sort")
    if isinstance(sort_specs, list) and sort_specs:
        specs_valid = [s for s in sort_specs if isinstance(s, dict) and (s.get("field") or s.get("column"))]
        for spec in reversed(specs_valid):
            fn = (spec.get("field") or spec.get("column") or "").strip()
            desc = (spec.get("direction") or "asc").strip().lower() == "desc"
            out.sort(
                key=lambda r, f=fn: _scalar_for_sort(_sort_key_value(r, f)),
                reverse=desc,
            )

    if apply_row_limit:
        lim = ordering_rules.get("limit")
        if lim is not None:
            try:
                n = int(lim)
            except (TypeError, ValueError):
                n = 0
            if n > 0:
                cap = _ordering_max_limit()
                out = out[: min(n, cap)]

    return out


def apply_deduplication_rules(data: list, deduplication_rules: dict) -> list:
    """
    Remove duplicate records based on user-specified fields
    
    Args:
        data: List of records to deduplicate
        deduplication_rules: Deduplication rules from AI with fields and strategy
        
    Returns:
        Deduplicated list of records
    
    Example deduplication_rules:
        {
            "enabled": true,
            "fields": ["Email", "Name"],
            "strategy": "keep_first"  # Options: "keep_first", "keep_last", "merge"
        }
    """
    if not deduplication_rules or not deduplication_rules.get("enabled"):
        return data
    
    fields = deduplication_rules.get("fields", [])
    strategy = deduplication_rules.get("strategy", "keep_first")
    
    if not fields:
        print("⚠️ Deduplication requested but no fields specified")
        return data
    
    # Normalize field names for case-insensitive matching
    normalized_fields = [f.lower() for f in fields]
    
    # Track seen records using a composite key
    seen_keys = {}
    deduplicated_records = []
    duplicate_count = 0
    
    for record in data:
        # Create a composite key from the specified fields (case-insensitive)
        key_parts = []
        for field in fields:
            # Try to find the field (case-insensitive)
            value = None
            for record_field, record_value in record.items():
                if record_field.lower() == field.lower():
                    # Normalize the value (lowercase, strip whitespace)
                    if isinstance(record_value, str):
                        value = record_value.lower().strip()
                    else:
                        value = str(record_value).lower().strip()
                    break
            
            key_parts.append(value if value else "")
        
        composite_key = "||".join(key_parts)
        
        # Apply deduplication strategy
        if strategy == "keep_first":
            if composite_key not in seen_keys:
                seen_keys[composite_key] = record
                deduplicated_records.append(record)
            else:
                duplicate_count += 1
                
        elif strategy == "keep_last":
            if composite_key in seen_keys:
                # Remove the previous occurrence
                deduplicated_records = [r for r in deduplicated_records if r != seen_keys[composite_key]]
                duplicate_count += 1
            seen_keys[composite_key] = record
            deduplicated_records.append(record)
            
        elif strategy == "merge":
            if composite_key not in seen_keys:
                seen_keys[composite_key] = record
                deduplicated_records.append(record)
            else:
                # Merge: combine non-empty values from duplicate into existing record
                existing_record = seen_keys[composite_key]
                for key, value in record.items():
                    # If existing record has empty/null value but duplicate has value, use it
                    if (not existing_record.get(key) or existing_record.get(key) == "") and value:
                        existing_record[key] = value
                duplicate_count += 1
        else:
            # Unknown strategy, default to keep_first
            if composite_key not in seen_keys:
                seen_keys[composite_key] = record
                deduplicated_records.append(record)
            else:
                duplicate_count += 1
    
    dedup_fields_display = ", ".join(fields)
    print(f"🔄 Deduplication ({strategy}): {len(data)} records → {len(deduplicated_records)} unique records (removed {duplicate_count} duplicates based on {dedup_fields_display})")
    
    return deduplicated_records


def apply_transformations(real_data: list, transformation_rules: dict) -> list:
    """
    Apply transformation rules to real data without sending it to OpenAI.
    This maintains SOC2/GDPR compliance while allowing intelligent data processing.
    
    Args:
        real_data: List of records with original field names from CSV/Excel
        transformation_rules: Rules returned by OpenAI for how to transform the data
        
    Returns:
        List of transformed records matching OpenAI's schema
    """
    # Validate transformation_rules format
    if not transformation_rules:
        return real_data
    
    # Handle case where transformation_rules might be a list instead of dict
    if isinstance(transformation_rules, list):
        transformation_rules = {"fields": transformation_rules}
    elif not isinstance(transformation_rules, dict):
        return real_data
    
    if not transformation_rules.get("fields"):
        return real_data
    
    transformed_records = []
    
    for record in real_data:
        # Start with a copy of the original record to preserve all fields
        # Only fields with "exclude" action will be removed
        new_record = dict(record)
        
        for rule in transformation_rules["fields"]:
            action = (rule.get("action") or "keep").lower()

            if action in ("merge", "combine"):
                source_fields = rule.get("source_fields")
                if source_fields is None:
                    source_fields = rule.get("source_field", [])
                if isinstance(source_fields, str):
                    source_fields = [source_fields]
                elif not isinstance(source_fields, list):
                    source_fields = []

                output_field = rule.get("output_field", "merge")
                separator = rule.get("separator", " ")

                # Resolve against new_record (current row state). If no source columns exist
                # (e.g. already merged in a prior version), skip — do not wipe output_field.
                matched_sources: List[str] = []
                seen_lower: set[str] = set()
                for src_field in source_fields:
                    matching_field = next(
                        (field for field in new_record.keys() if field.lower() == str(src_field).lower()),
                        None,
                    )
                    if matching_field and matching_field.lower() not in seen_lower:
                        seen_lower.add(matching_field.lower())
                        matched_sources.append(matching_field)

                if not matched_sources:
                    continue

                values = []
                for matching_field in matched_sources:
                    val = str(new_record.get(matching_field, "")).strip()
                    if val:
                        values.append(val)
                    if matching_field in new_record:
                        del new_record[matching_field]

                new_record[output_field] = separator.join(values)
                continue

            # Bulk column drop: LLM "remove" with fields[] → exclude + fields, or exploded excludes
            if action == "exclude":
                bulk = rule.get("fields")
                if isinstance(bulk, list) and bulk:
                    for fname in bulk:
                        if not isinstance(fname, str) or not fname.strip():
                            continue
                        target = fname.strip().lower()
                        for field in list(new_record.keys()):
                            if field.lower() == target:
                                del new_record[field]
                                break
                    continue

            if action == "keep_only":
                cols = rule.get("fields") or []
                allowed: Dict[str, str] = {}
                for col in cols:
                    if isinstance(col, str) and col.strip():
                        allowed[col.strip().lower()] = col.strip()
                projected: Dict[str, Any] = {}
                for field, val in new_record.items():
                    key = str(field).strip().lower()
                    if key in allowed:
                        projected[allowed[key]] = val
                new_record = projected
                continue

            if action in ("prepend", "append"):
                source_field_value = rule.get("source_field", "")
                if isinstance(source_field_value, list):
                    source_field_name = source_field_value[0] if source_field_value else ""
                else:
                    source_field_name = str(source_field_value) if source_field_value is not None else ""

                if not source_field_name or source_field_name == "None":
                    continue

                actual_field = None
                for field in new_record.keys():
                    if field.lower() == source_field_name.lower():
                        actual_field = field
                        break
                if not actual_field:
                    continue

                raw_val = new_record.get(actual_field, "")
                sval = "" if raw_val is None else str(raw_val).strip()
                if not sval:
                    new_record[actual_field] = ""
                    continue

                affix = str(rule.get("value", ""))
                affix_stripped = affix.strip()
                if action == "prepend":
                    if (
                        _field_name_suggests_phone(actual_field)
                        and _affix_targets_us_country_code(affix_stripped)
                    ):
                        new_val = normalize_phone_us_default_region(sval)
                    else:
                        new_val = (
                            sval
                            if affix_stripped and sval.startswith(affix_stripped)
                            else (affix + sval)
                        )
                else:
                    new_val = (
                        sval
                        if affix_stripped and sval.endswith(affix_stripped)
                        else (sval + affix)
                    )

                new_val = _maybe_format_us_phone_e164(new_val)

                out_raw = rule.get("output_field")
                out_name = (
                    out_raw.strip()
                    if isinstance(out_raw, str) and out_raw.strip()
                    else actual_field
                )
                # Resolve output key: reuse record casing if same logical field
                out_actual = None
                for field in new_record.keys():
                    if field.lower() == out_name.lower():
                        out_actual = field
                        break
                if out_actual is None:
                    out_actual = out_name

                if out_actual != actual_field:
                    new_record[out_actual] = new_val
                    if actual_field in new_record:
                        del new_record[actual_field]
                else:
                    new_record[actual_field] = new_val
                continue

            if action == "regex_replace":
                source_field_value = rule.get("source_field", "")
                if isinstance(source_field_value, list):
                    source_field_name = source_field_value[0] if source_field_value else ""
                else:
                    source_field_name = (
                        str(source_field_value) if source_field_value is not None else ""
                    )
                if not source_field_name or source_field_name == "None":
                    continue
                actual_field = None
                for field in new_record.keys():
                    if field.lower() == source_field_name.lower():
                        actual_field = field
                        break
                if not actual_field:
                    continue
                raw_val = new_record.get(actual_field, "")
                sval = "" if raw_val is None else str(raw_val).strip()
                if not sval:
                    continue
                pattern = rule.get("pattern")
                replacement = rule.get("replacement")
                if replacement is None:
                    replacement = ""
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                max_pat, max_sub = _data_ops_regex_limits()
                if len(pattern) > max_pat or len(sval) > max_sub:
                    continue
                try:
                    repl_norm = _re_sub_replacement_normalize_llm(str(replacement))
                    new_val = re.sub(pattern, repl_norm, sval)
                except re.error:
                    continue

                if _field_name_suggests_phone(actual_field):
                    collapsed_nv = re.sub(r"\s+", "", new_val.strip())
                    if _regex_replace_is_us_country_prepend(pattern, replacement) or (
                        "+1+" in collapsed_nv
                    ):
                        new_val = normalize_phone_us_default_region(new_val)
                    new_val = _maybe_format_us_phone_e164(new_val)

                out_raw = rule.get("output_field")
                out_name = (
                    out_raw.strip()
                    if isinstance(out_raw, str) and out_raw.strip()
                    else actual_field
                )
                out_actual = None
                for field in new_record.keys():
                    if field.lower() == out_name.lower():
                        out_actual = field
                        break
                if out_actual is None:
                    out_actual = out_name

                if out_actual != actual_field:
                    new_record[out_actual] = new_val
                    if actual_field in new_record:
                        del new_record[actual_field]
                else:
                    new_record[actual_field] = new_val
                continue

            source_field_value = rule.get("source_field", "")
            if isinstance(source_field_value, list):
                source_field_name = source_field_value[0] if source_field_value else ""
            else:
                source_field_name = str(source_field_value)

            if not source_field_name:
                continue

            # Find the actual field in record (case-insensitive matching)
            actual_field = None
            for field in record.keys():
                if field.lower() == source_field_name.lower():
                    actual_field = field
                    break

            if not actual_field:
                # Field doesn't exist in record - skip this rule
                # But don't exclude the record - it may have other valid fields
                continue

            value = str(record.get(actual_field, "")).strip()
            
            if action == "keep":
                # Simple field mapping/rename
                output_field = rule.get("output_field", source_field_name)
                # Check if output_field is explicitly provided (not just defaulting to source_field_name)
                output_field_explicitly_provided = "output_field" in rule
                
                # If output_field is explicitly provided and differs from actual_field (case-sensitive),
                # OR if output_field differs case-insensitively, rename it
                if output_field_explicitly_provided and output_field != actual_field:
                    # Explicit rename (case-sensitive check)
                    new_record[output_field] = value
                    # Remove original field if it was renamed
                    if actual_field in new_record:
                        del new_record[actual_field]
                elif not output_field_explicitly_provided and output_field.lower() != actual_field.lower():
                    # Implicit rename (case-insensitive check when output_field not explicitly provided)
                    new_record[output_field] = value
                    # Remove original field if it was renamed
                    if actual_field in new_record:
                        del new_record[actual_field]
                # Otherwise, field already exists in new_record (from dict(record)) with correct name
                
            elif action == "exclude":
                # Remove this field from the record
                # For exclude, we need to check both source_field and output_field
                # because the field might have been renamed in previous transformations
                # (output_field takes precedence if provided)
                field_to_exclude = None
                
                # First, try output_field if provided (field might have been renamed)
                output_field = rule.get("output_field")
                if output_field:
                    # Find the actual field in record (case-insensitive matching)
                    for field in record.keys():
                        if field.lower() == str(output_field).lower():
                            field_to_exclude = field
                            break
                
                # If output_field didn't match, use source_field (already found as actual_field)
                if not field_to_exclude:
                    field_to_exclude = actual_field
                
                # Remove the field if found
                if field_to_exclude and field_to_exclude in new_record:
                    del new_record[field_to_exclude]
                
            elif action.startswith("split_"):
                # Dynamic splitting (split_2, split_3, etc.)
                try:
                    num_parts = int(action.split("_")[1])
                    split_char = rule.get("split_char", " ")
                    output_fields = rule.get("output_fields", [])
                    
                    if not value:
                        # Empty value - set all output fields to empty
                        for output_field in output_fields:
                            new_record[output_field] = ""
                    else:
                        from utils.split_field_utils import (
                            resolve_split_required_indices,
                            split_value_into_parts,
                        )

                        required_indices = resolve_split_required_indices(
                            output_fields,
                            rule=rule,
                        )
                        parts = split_value_into_parts(
                            value,
                            output_fields,
                            split_char=split_char,
                            required_indices=required_indices,
                        )
                        for i, output_field in enumerate(output_fields):
                            new_record[output_field] = parts[i] if i < len(parts) else ""
                    
                    # Remove the original field after splitting
                    if actual_field in new_record:
                        del new_record[actual_field]
                            
                except (ValueError, IndexError) as e:
                    print(f"⚠️ Error splitting field {source_field_name}: {e}")
                    # Fallback: put entire value in first output field
                    if rule.get("output_fields"):
                        new_record[rule["output_fields"][0]] = value
                        # Remove original field
                        if actual_field in new_record:
                            del new_record[actual_field]
                        
            elif action == "split_address":
                # Parse address into components
                output_fields = rule.get("output_fields", ["MailingStreet", "MailingCity", "MailingState", "MailingCountry", "MailingPostalCode"])
                
                if not value:
                    for field in output_fields:
                        new_record[field] = ""
                else:
                    address_parts = parse_address_string(value)
                    for field in output_fields:
                        new_record[field] = address_parts.get(field, "")
                
                # Remove the original field after splitting
                if actual_field in new_record:
                    del new_record[actual_field]
            else:
                # Unknown action - keep as-is (already in new_record from dict(record))
                print(f"⚠️ Unknown transformation action: {action}")
        
        # Only append non-empty records (records should always have at least some fields)
        # This prevents completely empty records from being added
        if new_record:
            transformed_records.append(new_record)
    
    return transformed_records


def parse_address_string(address: str) -> dict:
    """
    Parse an address string into components.
    Example: "123 Montgomery st., Atlanta, Georgia, United States, 94103"
    """
    import re
    
    components = {
        "MailingStreet": "",
        "MailingCity": "",
        "MailingState": "",
        "MailingCountry": "",
        "MailingPostalCode": ""
    }
    
    if not address:
        return components
    
    # Split by comma
    parts = [p.strip() for p in address.split(",")]
    
    if len(parts) >= 1:
        components["MailingStreet"] = parts[0]
    if len(parts) >= 2:
        components["MailingCity"] = parts[1]
    if len(parts) >= 3:
        components["MailingState"] = parts[2]
    if len(parts) >= 4:
        # Could be country or postal code
        # Simple heuristic: if it's all digits, it's postal code
        if parts[3].replace("-", "").replace(" ", "").isdigit():
            components["MailingPostalCode"] = parts[3]
        else:
            components["MailingCountry"] = parts[3]
    if len(parts) >= 5:
        components["MailingPostalCode"] = parts[4]
    
    # Try to extract postal code from last part if not found
    if not components["MailingPostalCode"] and parts:
        postal_match = re.search(r'\b\d{5}(?:-\d{4})?\b', parts[-1])
        if postal_match:
            components["MailingPostalCode"] = postal_match.group()
    
    return components


def apply_cleanup_and_validation_rules(real_data: list, ai_output: list) -> list:
    """
    Apply AI-generated cleanup and validation rules to real customer data
    This processes the real data using rules generated from sample data
    """

    if not real_data or not ai_output:
        return real_data
    
    # Extract cleanup rules from AI output metadata
    cleanup_rules = {}
    if ai_output and len(ai_output) > 0:
        first_record = ai_output[0]
        cleanup_rules = first_record.get('data_cleanup_rules', {})
        # Removed verbose debug log: extracted cleanup rules summary
    
    # If no cleanup rules provided (null or empty), return original data
    if not cleanup_rules or cleanup_rules is None:
        # No cleanup rules provided - return original data
        return real_data
    
    # Removed verbose debug log: applying cleanup rules summary
    # Apply rules to real data
    processed_data = []
    for record in real_data:
        processed_record = record.copy()
        
        # Apply field-specific cleanup rules
        for field, rules in cleanup_rules.items():
            # Try exact match first
            actual_field = None
            if field in processed_record:
                actual_field = field
            else:
                # Fallback: case-insensitive and naming convention matching
                # This handles cases where field names differ by case or naming convention
                # (e.g., "first_name" in rules vs "FirstName" in data)
                # Normalize field name by: lowercase + remove underscores
                def normalize_field_name(field_name: str) -> str:
                    """Normalize field name for comparison (lowercase, remove underscores)"""
                    return field_name.lower().replace('_', '').replace('-', '')
                
                field_normalized = normalize_field_name(field)
                for record_field in processed_record.keys():
                    record_field_normalized = normalize_field_name(record_field)
                    if record_field_normalized == field_normalized:
                        actual_field = record_field
                        break
            
            if actual_field:
                if not isinstance(rules, dict):
                    continue
                value = processed_record.get(actual_field)

                if rules.get("trim") and isinstance(value, str):
                    processed_record[actual_field] = value.strip()
                    value = processed_record[actual_field]

                rr = rules.get("regex_replace")
                if isinstance(rr, dict) and isinstance(value, str):
                    pat = rr.get("pattern")
                    rep = rr.get("replacement", "")
                    if isinstance(pat, str) and pat.strip():
                        max_pat, max_sub = _data_ops_regex_limits()
                        if len(pat) <= max_pat and len(value) <= max_sub:
                            try:
                                max_rep = 999
                                cnt_raw = rr.get("max_count")
                                try:
                                    cnt = int(cnt_raw) if cnt_raw is not None else max_rep
                                except (TypeError, ValueError):
                                    cnt = max_rep
                                cnt = max(1, min(cnt, max_rep))
                                new_v = re.sub(pat, str(rep), value, count=cnt)
                            except re.error:
                                new_v = value
                            processed_record[actual_field] = new_v
                            value = new_v

                if isinstance(value, str) and rules.get("normalize_country"):
                    processed_record[actual_field] = _cleanup_country_alpha2(value, rules)
                    value = processed_record[actual_field]

                if rules.get("type") == "email" and isinstance(value, str):
                    processed_record[actual_field] = value.strip().lower()
                    value = processed_record[actual_field]

                elif rules.get("type") == "phone" and isinstance(value, str):
                    if str(rules.get("format", "")).lower() == "e164":
                        processed_record[actual_field] = _cleanup_phone_e164(value, rules)
                    else:
                        processed_record[actual_field] = "".join(
                            c for c in value if c.isdigit() or c in "+-"
                        )
                    value = processed_record[actual_field]

                elif rules.get("type") == "date" and isinstance(value, str):
                    s = value.strip()
                    if s:
                        parsed = None
                        fmts = rules.get("input_formats")
                        if isinstance(fmts, list):
                            for fmt in fmts:
                                if isinstance(fmt, str):
                                    try:
                                        parsed = datetime.strptime(s, fmt)
                                        break
                                    except ValueError:
                                        continue
                        if parsed is None:
                            try:
                                parsed = date_parser.parse(s, dayfirst=False, yearfirst=True)
                            except (ValueError, TypeError, OverflowError, AttributeError):
                                parsed = None
                        if parsed is not None:
                            if rules.get("to_iso"):
                                processed_record[actual_field] = parsed.date().isoformat()
                            else:
                                processed_record[actual_field] = parsed.isoformat()
                            value = processed_record[actual_field]

                cur = processed_record.get(actual_field)
                if rules.get("normalize_case") == "title" and isinstance(cur, str):
                    processed_record[actual_field] = cur.title()
                elif rules.get("normalize_case") == "upper" and isinstance(cur, str):
                    processed_record[actual_field] = cur.upper()
                elif rules.get("normalize_case") == "lower" and isinstance(cur, str):
                    processed_record[actual_field] = cur.lower()

                value = processed_record.get(actual_field)
                if rules.get("handle_empty") == "skip" and (
                    value is None or (isinstance(value, str) and value.strip() == "")
                ):
                    processed_record.pop(actual_field, None)
                    continue

                value = processed_record.get(actual_field)
                if rules.get("fill_missing") is not None and (
                    value is None or (isinstance(value, str) and value.strip() == "")
                ):
                    processed_record[actual_field] = rules.get("fill_missing")
        
        processed_data.append(processed_record)
    
    return processed_data


# ============================================================================
# PDF EXTRACTION FUNCTIONS
# ============================================================================

def extract_tables_from_pdf_with_textract(pdf_path: str) -> list:
    """
    Extract tables from PDF using AWS Textract
    Returns list of records (dictionaries) with table data
    """
    import boto3
    
    try:
        # Initialize Textract client
        textract = boto3.client('textract', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        
        # Read PDF file
        with open(pdf_path, 'rb') as document:
            document_bytes = document.read()
        
        # Call Textract
        response = textract.analyze_document(
            Document={'Bytes': document_bytes},
            FeatureTypes=['TABLES']
        )
        
        # Parse tables from response
        blocks = response.get('Blocks', [])
        
        # Group blocks by type
        tables = [b for b in blocks if b['BlockType'] == 'TABLE']
        cells = {b['Id']: b for b in blocks if b['BlockType'] == 'CELL'}
        
        if not tables:
            return []
        
        # Process each table
        all_records = []
        
        for table in tables:
            # Get cells for this table
            table_cells = []
            if 'Relationships' in table:
                for relationship in table['Relationships']:
                    if relationship['Type'] == 'CHILD':
                        for cell_id in relationship['Ids']:
                            if cell_id in cells:
                                table_cells.append(cells[cell_id])
            
            # Organize cells by row and column
            rows = {}
            for cell in table_cells:
                row_index = cell.get('RowIndex', 1)
                col_index = cell.get('ColumnIndex', 1)
                
                if row_index not in rows:
                    rows[row_index] = {}
                
                # Get cell text
                cell_text = ""
                if 'Relationships' in cell:
                    for relationship in cell['Relationships']:
                        if relationship['Type'] == 'CHILD':
                            for word_id in relationship['Ids']:
                                word_block = next((b for b in blocks if b['Id'] == word_id), None)
                                if word_block and word_block.get('Text'):
                                    cell_text += word_block['Text'] + " "
                
                rows[row_index][col_index] = cell_text.strip()
            
            # Convert to records
            if not rows or len(rows) < 2:
                continue
            
            # First row is headers
            header_row = rows.get(1, {})
            max_cols = max(rows[1].keys()) if rows.get(1) else 0
            
            # Create headers list ensuring all columns are represented
            headers = []
            for col_idx in range(1, max_cols + 1):
                header_text = header_row.get(col_idx, f"Column{col_idx}")
                if not header_text.strip():
                    header_text = f"Column{col_idx}"
                headers.append(header_text.strip())
            
            # Process data rows
            for row_idx in sorted(rows.keys()):
                if row_idx == 1:  # Skip header row
                    continue
                
                row_data = {}
                for col_idx, header in enumerate(headers, start=1):
                    # Always include the header even if the cell is empty
                    cell_value = rows[row_idx].get(col_idx, "")
                    row_data[header] = cell_value
                
                # 🧹 Filter out empty rows (consistent with CSV/Excel dropna(how='all') behavior)
                # Only append if row has at least one non-empty value
                if any(str(value).strip() for value in row_data.values()):
                    all_records.append(row_data)
        
        return all_records
        
    except Exception as e:
        print(f"Error extracting tables from PDF: {e}")
        return []


def extract_text_from_pdf_with_textract(pdf_path: str) -> str:
    """
    Extract text from PDF using AWS Textract
    """
    import boto3
    
    try:
        textract = boto3.client('textract', region_name=os.getenv('AWS_REGION', 'us-east-1'))
        
        with open(pdf_path, 'rb') as document:
            document_bytes = document.read()
        
        response = textract.detect_document_text(
            Document={'Bytes': document_bytes}
        )
        
        # Extract text from blocks
        text = ""
        for block in response.get('Blocks', []):
            if block['BlockType'] == 'LINE':
                text += block.get('Text', '') + "\n"
        
        return text
        
    except Exception as e:
        print(f"Error extracting text from PDF: {e}")
        return ""


# ============================================================================
# MAIN EXTRACTION FUNCTIONS
# ============================================================================

