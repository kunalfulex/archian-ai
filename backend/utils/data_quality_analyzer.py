"""
Data Quality Analyzer - Programmatic analysis of extracted data
Analyzes data for duplicates, missing values, format issues, and inconsistencies
"""

import json
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
from email_validator import validate_email, EmailNotValidError
from dateutil import parser as date_parser
from typing import List, Dict, Any, Optional
from difflib import SequenceMatcher


def format_quality_report_as_text(report: Dict[str, Any]) -> str:
    """
    Converts structured data quality report into plain English text.
    
    Returns a human-readable summary suitable for display in chat messages.
    """
    if not report or not report.get("summary"):
        return ""
    
    summary = report.get("summary", {})
    quality_score = summary.get("data_quality_score", 0)
    total_issues = summary.get("total_issues_found", 0)
    total_records = report.get("total_records", 0)
    
    # Build plain English text
    lines = []
    
    # Header with quality score
    lines.append(f"**Data Quality Analysis** (Score: {quality_score:.1f}/100)")
    lines.append("")
    
    # Overall status
    if quality_score == 100:
        lines.append("✅ Your data looks great!")
    elif quality_score >= 90:
        lines.append("✅ Your data looks great! Only minor issues detected.")
    elif quality_score >= 70:
        lines.append("⚠️ Your data has some quality issues that should be addressed.")
    else:
        lines.append("❌ Your data has significant quality issues that need attention.")
    
    lines.append("")
    
    # Duplicates section
    duplicates = report.get("duplicates") or {}
    exact_dups_dict = duplicates.get("exact_duplicates") or {}
    exact_dups = exact_dups_dict.get("count", 0) if isinstance(exact_dups_dict, dict) else 0
    key_dups_dict = duplicates.get("key_field_duplicates") or {}
    key_dups = key_dups_dict.get("count", 0) if isinstance(key_dups_dict, dict) else 0
    
    if exact_dups > 0 or key_dups > 0:
        lines.append("**Duplicates Found:**")
        if exact_dups > 0:
            lines.append(f"  • {exact_dups} exact duplicate record(s)")
        if key_dups > 0:
            key_fields_dict = duplicates.get("key_field_duplicates") or {}
            key_fields = key_fields_dict.get("fields_checked", []) if isinstance(key_fields_dict, dict) else []
            if isinstance(key_fields, list):
                fields_str = ", ".join(key_fields[:3])  # Limit to first 3 fields
                if len(key_fields) > 3:
                    fields_str += "..."
                lines.append(f"  • {key_dups} potential duplicate(s) based on key fields ({fields_str})")
            else:
                lines.append(f"  • {key_dups} potential duplicate(s) based on key fields")
        lines.append("")
    
    # Missing values section
    missing = report.get("missing_values", {})
    total_missing = missing.get("total_missing", 0)
    by_field = missing.get("by_field", {})
    
    if total_missing > 0:
        lines.append("**Missing Values:**")
        lines.append(f"  • {total_missing} missing value(s) across {len(by_field)} field(s)")
        # Show top 3 fields with most missing values
        top_missing = sorted(by_field.items(), key=lambda x: x[1], reverse=True)[:3]
        percentages = missing.get("percentages") or {}
        for field, count in top_missing:
            percentage = percentages.get(field, 0) if isinstance(percentages, dict) else 0
            lines.append(f"  • {field}: {count} missing ({percentage}%)")
        lines.append("")
    
    # Format issues section
    format_issues = report.get("format_issues", {})
    if format_issues:
        lines.append("**Format Issues:**")
        email_issues = [k for k in format_issues.keys() if "invalid_emails" in k]
        date_issues = [k for k in format_issues.keys() if "invalid_dates" in k]
        if email_issues:
            # Count total invalid emails
            total_invalid_emails = sum(len(format_issues[k]) for k in email_issues)
            lines.append(f"  • {total_invalid_emails} invalid email address(es)")
        if date_issues:
            total_invalid_dates = sum(len(format_issues[k]) for k in date_issues)
            lines.append(f"  • {total_invalid_dates} invalid date format(s)")
        lines.append("")
    
    # Inconsistencies section
    inconsistencies = report.get("inconsistencies", {})
    if inconsistencies:
        lines.append("**Data Inconsistencies:**")
        case_issue_keys = [k for k in inconsistencies.keys() if "case_inconsistency" in k]
        fuzzy_dups = sum(1 for k in inconsistencies.keys() if "fuzzy_duplicates" in k)
        
        if case_issue_keys:
            # Collect field names and examples for case inconsistencies
            case_fields_with_examples = []
            for key in case_issue_keys:
                field_name = key.replace("case_inconsistency_", "")
                inconsistency_data = inconsistencies[key]
                example_values = inconsistency_data.get("example_values", [])
                if example_values:
                    # Format examples with quotes and join with commas
                    examples_str = ", ".join([f"'{ex}'" for ex in example_values[:3]])
                    case_fields_with_examples.append({
                        "field": field_name,
                        "examples": examples_str
                    })
            
            # Format the message with field names and examples
            if len(case_fields_with_examples) == 1:
                field_info = case_fields_with_examples[0]
                lines.append(f"  • Field '{field_info['field']}' has inconsistent capitalization (e.g., {field_info['examples']} appear in the same field)")
            elif len(case_fields_with_examples) > 1:
                # Multiple fields - show first 2-3 with examples, then summarize the rest
                fields_to_show = case_fields_with_examples[:2]
                field_descriptions = []
                for field_info in fields_to_show:
                    field_descriptions.append(f"'{field_info['field']}' (e.g., {field_info['examples']})")
                
                if len(case_fields_with_examples) == 2:
                    lines.append(f"  • {len(case_fields_with_examples)} fields have inconsistent capitalization: {', '.join(field_descriptions)}")
                else:
                    remaining_count = len(case_fields_with_examples) - 2
                    lines.append(f"  • {len(case_fields_with_examples)} fields have inconsistent capitalization: {', '.join(field_descriptions)}, and {remaining_count} more field(s)")
            
        if fuzzy_dups > 0:
            lines.append(f"  • Similar but not identical values detected (potential duplicates)")
        lines.append("")
    
    # Recommendations (limit to top 5)
    recommendations = summary.get("recommendations", [])
    if recommendations:
        lines.append("**Recommendations:**")
        for rec in recommendations[:5]:
            lines.append(f"  • {rec}")
        lines.append("")
    
    # Call to action (only if issues found)
    if total_issues > 0:
        lines.append("💡 Would you like to start a data cleanup process to fix these issues?")
    
    return "\n".join(lines)


def _normalize_cell_for_analysis(value: Any) -> Any:
    """Convert nested/unhashable values so pandas can compare and dedupe rows."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (dict, list, set, tuple)):
        try:
            return json.dumps(value, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if hasattr(value, "item") and callable(value.item):
        try:
            return value.item()
        except (ValueError, AttributeError):
            return str(value)
    return value


def analyze_data_quality(
    data: List[Dict[str, Any]],
    *,
    omit_record_indices: bool = False,
) -> Dict[str, Any]:
    """
    Analyzes extracted data for quality issues using programmatic checks.
    
    Performs the following analyses:
    1. Duplicate detection (exact and key field duplicates)
    2. Missing values analysis
    3. Format validation (emails, dates)
    4. Data inconsistencies (case, type)
    
    Args:
        data: List of records (dictionaries) to analyze
        omit_record_indices: When True, strip row indices from output (assessment reports).
        
    Returns:
        Dictionary with quality analysis results including:
        - duplicates: Information about duplicate records
        - missing_values: Missing/null/empty value statistics
        - format_issues: Invalid email/date formats
        - inconsistencies: Case/type inconsistencies
        - summary: Overall quality score and recommendations
    """
    if not data or len(data) == 0:
        return {
            "total_records": 0,
            "summary": {
                "total_issues_found": 0,
                "data_quality_score": 100,
                "has_issues": False,
                "recommendations": []
            }
        }
    
    # Filter metadata fields and normalize nested types (JSON columns from SQL/Sheets).
    filtered_data = []
    for record in data:
        filtered_record = {
            k: _normalize_cell_for_analysis(v)
            for k, v in record.items()
            if not k.startswith("_")
        }
        filtered_data.append(filtered_record)
    
    # Convert to pandas DataFrame for easier analysis
    df = pd.DataFrame(filtered_data)
    total_records = len(data)
    
    # Early return if DataFrame is empty after filtering
    if df.empty or len(df) == 0:
        return {
            "total_records": total_records,
            "summary": {
                "total_issues_found": 0,
                "data_quality_score": 100,
                "has_issues": False,
                "recommendations": []
            }
        }
    
    quality_report = {
        "total_records": total_records,
        "duplicates": {},
        "missing_values": {},
        "format_issues": {},
        "inconsistencies": {},
        "summary": {}
    }
    
    try:
        # 1. FIND DUPLICATES using pandas
        # Exact duplicates (all columns match)
        # Reset index to avoid alignment issues when filtering
        df = df.reset_index(drop=True)
        
        # Ensure we have at least 2 records for duplicate detection, otherwise skip
        if len(df) < 2:
            quality_report["duplicates"]["exact_duplicates"] = {
                "count": 0,
                "record_indices": []
            }
        else:
            duplicate_mask = df.duplicated(keep=False)
            # Ensure mask index matches DataFrame index
            if len(duplicate_mask) == len(df) and duplicate_mask.index.equals(df.index):
                exact_duplicates = df[duplicate_mask]
            else:
                # Reindex mask to match DataFrame if needed
                duplicate_mask = duplicate_mask.reindex(df.index, fill_value=False)
                exact_duplicates = df[duplicate_mask]
            
            quality_report["duplicates"]["exact_duplicates"] = {
                "count": len(exact_duplicates),
                "record_indices": exact_duplicates.index.tolist() if len(exact_duplicates) > 0 else []
            }
        
        # Key field duplicates (email, name, phone combinations)
        key_fields = []
        for col in df.columns:
            col_lower = str(col).lower()
            if any(keyword in col_lower for keyword in ['email', 'name', 'phone', 'id']):
                key_fields.append(col)
        
        key_field_duplicates = pd.DataFrame()
        if key_fields:
            duplicate_keys = df[df.duplicated(subset=key_fields, keep=False)]
            quality_report["duplicates"]["key_field_duplicates"] = {
                "count": len(duplicate_keys),
                "fields_checked": key_fields,
                "record_indices": duplicate_keys.index.tolist() if len(duplicate_keys) > 0 else []
            }
        else:
            quality_report["duplicates"]["key_field_duplicates"] = {
                "count": 0,
                "fields_checked": [],
                "record_indices": []
            }
        
        # 2. FIND MISSING VALUES using pandas
        # Count null, empty string, and 'N/A' values
        missing_counts = df.isnull().sum() + (df == '').sum() + (df == 'N/A').sum() + (df == 'n/a').sum()
        missing_data = missing_counts[missing_counts > 0]
        
        quality_report["missing_values"] = {
            "total_missing": int(missing_counts.sum()),
            "by_field": {str(k): int(v) for k, v in missing_data.items()},
            "percentages": {str(k): round((v/total_records)*100, 2) for k, v in missing_data.items()}
        }
        
        # 3. FIND FORMAT ISSUES
        format_issues = {}
        
        # Email validation using email_validator
        email_fields = [col for col in df.columns if 'email' in str(col).lower()]
        for email_field in email_fields:
            invalid_emails = []
            for idx, email in enumerate(df[email_field]):
                if pd.notna(email) and email and str(email).strip():
                    try:
                        validate_email(str(email), check_deliverability=False)
                    except EmailNotValidError:
                        invalid_emails.append({"index": int(idx), "value": str(email)})
            if invalid_emails:
                format_issues[f"invalid_emails_{email_field}"] = invalid_emails
        
        # Date format validation using dateutil
        date_fields = [
            col for col in df.columns 
            if any(keyword in str(col).lower() for keyword in ['date', 'time', 'created', 'updated', 'modified'])
        ]
        for date_field in date_fields:
            invalid_dates = []
            for idx, date_val in enumerate(df[date_field]):
                if pd.notna(date_val) and date_val and str(date_val).strip():
                    try:
                        date_parser.parse(str(date_val))
                    except (ValueError, TypeError):
                        invalid_dates.append({"index": int(idx), "value": str(date_val)})
            if invalid_dates:
                format_issues[f"invalid_dates_{date_field}"] = invalid_dates
        
        quality_report["format_issues"] = format_issues
        
        # 4. FIND INCONSISTENCIES
        inconsistencies = {}
        
        # Case inconsistencies in categorical fields (limit to avoid performance issues)
        categorical_fields = [col for col in df.columns if df[col].dtype == 'object']
        for field in categorical_fields[:10]:  # Limit to first 10 fields
            values = df[field].dropna().astype(str)
            if len(values) > 0:
                unique_cases = set()
                examples_by_case = {'lower': [], 'upper': [], 'title': [], 'mixed': []}
                
                for val in values:
                    if val:
                        if val.islower():
                            unique_cases.add('lower')
                            if len(examples_by_case['lower']) < 2:
                                examples_by_case['lower'].append(val)
                        elif val.isupper():
                            unique_cases.add('upper')
                            if len(examples_by_case['upper']) < 2:
                                examples_by_case['upper'].append(val)
                        elif val.istitle():
                            unique_cases.add('title')
                            if len(examples_by_case['title']) < 2:
                                examples_by_case['title'].append(val)
                        else:
                            unique_cases.add('mixed')
                            if len(examples_by_case['mixed']) < 2:
                                examples_by_case['mixed'].append(val)
                
                if len(unique_cases) > 1:
                    # Collect example values from different case types (up to 3 examples total)
                    example_values = []
                    for case_type in ['lower', 'upper', 'title', 'mixed']:
                        if case_type in unique_cases and examples_by_case[case_type]:
                            example_values.extend(examples_by_case[case_type][:1])  # Take 1 example from each case type
                            if len(example_values) >= 3:
                                break
                    
                    inconsistencies[f"case_inconsistency_{field}"] = {
                        "case_types_found": list(unique_cases),
                        "unique_values_count": int(len(values.unique())),
                        "example_values": example_values[:3]  # Limit to 3 examples
                    }
        
        # Find potential fuzzy duplicates for name fields (only for smaller datasets for performance)
        if total_records <= 1000:
            name_fields = [col for col in df.columns if 'name' in str(col).lower()]
            for name_field in name_fields[:1]:  # Limit to one field
                fuzzy_duplicates = []
                values = df[name_field].dropna().astype(str).tolist()
                seen = set()
                for i, val1 in enumerate(values):
                    if val1 in seen or not val1.strip():
                        continue
                    matches = []
                    for j, val2 in enumerate(values[i+1:], start=i+1):
                        if not val2.strip():
                            continue
                        similarity = SequenceMatcher(None, val1.lower().strip(), val2.lower().strip()).ratio()
                        if similarity > 0.85 and similarity < 1.0:  # Similar but not exact
                            matches.append({"index": int(j), "value": str(val2), "similarity": round(similarity, 2)})
                    if matches:
                        fuzzy_duplicates.append({
                            "index": int(i),
                            "value": str(val1),
                            "similar_matches": matches
                        })
                        seen.add(val1)
                if fuzzy_duplicates:
                    inconsistencies[f"fuzzy_duplicates_{name_field}"] = fuzzy_duplicates
        
        quality_report["inconsistencies"] = inconsistencies
        
        # 5. SUMMARY STATISTICS
        total_issues = (
            quality_report["duplicates"]["exact_duplicates"]["count"] +
            quality_report["missing_values"]["total_missing"] +
            len(quality_report["format_issues"]) +
            len(quality_report["inconsistencies"])
        )
        
        # Calculate quality score (0-100)
        # Penalize based on issues found
        quality_score = max(0, 100 - min(100, (total_issues / max(total_records, 1)) * 100))
        
        # Generate recommendations
        recommendations = []
        if quality_report["duplicates"]["exact_duplicates"]["count"] > 0:
            recommendations.append(f"Remove {quality_report['duplicates']['exact_duplicates']['count']} exact duplicate records")
        if quality_report["duplicates"]["key_field_duplicates"]["count"] > 0:
            kfd = quality_report["duplicates"]["key_field_duplicates"]
            fields_checked = kfd.get("fields_checked") or []
            if fields_checked:
                fields_str = ", ".join(str(f) for f in fields_checked)
                recommendations.append(
                    f"Review {kfd['count']} potential duplicate records (key fields: {fields_str})"
                )
            else:
                recommendations.append(
                    f"Review {kfd['count']} potential duplicate records"
                )
        if quality_report["missing_values"]["total_missing"] > 0:
            top_missing_fields = sorted(
                quality_report["missing_values"]["by_field"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:3]
            if top_missing_fields:
                field_names = ", ".join([f"{field} ({count})" for field, count in top_missing_fields])
                recommendations.append(f"Fill missing values in: {field_names}")
        if format_issues:
            recommendations.append(f"Fix {len(format_issues)} format validation issues")
        if not recommendations:
            recommendations.append("Data quality looks good!")
        
        quality_report["summary"] = {
            "total_issues_found": total_issues,
            "data_quality_score": round(quality_score, 2),
            "has_issues": total_issues > 0,
            "recommendations": recommendations
        }
        
        # Generate plain English text summary
        quality_report["summary_text"] = format_quality_report_as_text(quality_report)

        if omit_record_indices:
            _strip_record_indices_from_report(quality_report)
        
    except Exception as e:
        print(f"⚠️ Error during data quality analysis: {e}")
        import traceback
        traceback.print_exc()
        # Return minimal report on error
        quality_report["summary"] = {
            "total_issues_found": 0,
            "data_quality_score": 0,
            "has_issues": False,
            "recommendations": ["Unable to analyze data quality"],
            "error": str(e)
        }
        quality_report["summary_text"] = ""
    
    if omit_record_indices and quality_report:
        _strip_record_indices_from_report(quality_report)
    return quality_report


def _strip_record_indices_from_report(quality_report: Dict[str, Any]) -> None:
    dups = quality_report.get("duplicates") or {}
    for key in ("exact_duplicates", "key_field_duplicates"):
        block = dups.get(key)
        if isinstance(block, dict):
            block.pop("record_indices", None)
    fmt = quality_report.get("format_issues") or {}
    for fk in list(fmt.keys()):
        issues = fmt.get(fk)
        if isinstance(issues, list) and len(issues) > 10:
            fmt[fk] = issues[:10]


def merge_quality_profiles(
    left: Optional[Dict[str, Any]],
    right: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge two quality reports from sequential chunks (approximate aggregates)."""
    if not left:
        return right
    if not right:
        return left

    total_records = int(left.get("total_records") or 0) + int(right.get("total_records") or 0)
    merged: Dict[str, Any] = {
        "total_records": total_records,
        "duplicates": {},
        "missing_values": {"total_missing": 0, "by_field": {}, "percentages": {}},
        "format_issues": {},
        "inconsistencies": {},
        "summary": {},
    }

    for side in (left, right):
        dups = side.get("duplicates") or {}
        for dk in ("exact_duplicates", "key_field_duplicates"):
            block = dups.get(dk) or {}
            if dk not in merged["duplicates"]:
                merged["duplicates"][dk] = {"count": 0, "fields_checked": [], "record_indices": []}
            merged["duplicates"][dk]["count"] = int(merged["duplicates"][dk].get("count") or 0) + int(
                block.get("count") or 0
            )
            if block.get("fields_checked"):
                merged["duplicates"][dk]["fields_checked"] = list(
                    set(merged["duplicates"][dk].get("fields_checked") or [])
                    | set(block.get("fields_checked") or [])
                )
        mv = side.get("missing_values") or {}
        merged["missing_values"]["total_missing"] += int(mv.get("total_missing") or 0)
        for field, cnt in (mv.get("by_field") or {}).items():
            merged["missing_values"]["by_field"][field] = int(
                merged["missing_values"]["by_field"].get(field) or 0
            ) + int(cnt)
        for fk, issues in (side.get("format_issues") or {}).items():
            merged["format_issues"].setdefault(fk, [])
            if isinstance(issues, list):
                merged["format_issues"][fk].extend(issues)
        for ik, val in (side.get("inconsistencies") or {}).items():
            merged["inconsistencies"][ik] = val

    if total_records > 0:
        for field, cnt in merged["missing_values"]["by_field"].items():
            merged["missing_values"]["percentages"][field] = round(
                (cnt / total_records) * 100, 2
            )

    total_issues = (
        int(merged["duplicates"].get("exact_duplicates", {}).get("count") or 0)
        + int(merged["duplicates"].get("key_field_duplicates", {}).get("count") or 0)
        + int(merged["missing_values"]["total_missing"])
        + len(merged["format_issues"])
        + len(merged["inconsistencies"])
    )
    quality_score = max(0, 100 - min(100, (total_issues / max(total_records, 1)) * 100))
    merged["summary"] = {
        "total_issues_found": total_issues,
        "data_quality_score": round(quality_score, 2),
        "has_issues": total_issues > 0,
        "recommendations": (left.get("summary") or {}).get("recommendations", [])
        + (right.get("summary") or {}).get("recommendations", []),
    }
    merged["summary"]["recommendations"] = list(dict.fromkeys(merged["summary"]["recommendations"]))[:10]
    merged["summary_text"] = format_quality_report_as_text(merged)
    _strip_record_indices_from_report(merged)
    return merged


def convert_quality_report_to_extraction_rules(quality_report: Dict[str, Any], actual_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    Convert data quality report into extraction_rules format for reuse with existing extraction infrastructure.
    
    Maps:
    1. Duplicates → deduplication_rules
    2. Case inconsistencies → data_cleanup_rules (with normalize_case)
    3. Format issues (emails) → data_cleanup_rules (with type: "email")
    
    Args:
        quality_report: Data quality report from analyze_data_quality()
        actual_data: Optional list of actual data records being cleaned. If provided, field names
                    from quality report will be mapped to actual field names in the data using
                    case-insensitive matching.
        
    Returns:
        Dictionary with extraction_rules structure:
        {
            "deduplication_rules": {...},
            "data_cleanup_rules": {...}
        }
    """
    extraction_rules = {
        "deduplication_rules": None,
        "data_cleanup_rules": {}
    }
    
    if not quality_report:
        return extraction_rules
    
    duplicates = quality_report.get("duplicates", {})
    inconsistencies = quality_report.get("inconsistencies", {})
    format_issues = quality_report.get("format_issues", {})
    
    # Helper function to normalize field names for comparison
    def normalize_field_name(field_name: str) -> str:
        """Normalize field name for comparison (lowercase, remove underscores and hyphens)"""
        return field_name.lower().replace('_', '').replace('-', '')
    
    # Helper function to map field names from quality report to actual data field names
    def map_field_name(report_field_name: str) -> str:
        """Map field name from quality report to actual field name in data (normalized matching)"""
        if not actual_data or not actual_data:
            return report_field_name
        
        # Get field names from actual data (use first record as sample)
        first_record = actual_data[0] if isinstance(actual_data, list) and len(actual_data) > 0 else {}
        if not isinstance(first_record, dict):
            return report_field_name
        
        # Try exact match first
        if report_field_name in first_record:
            return report_field_name
        
        # Normalized match (handles case differences and naming conventions)
        # e.g., "first_name" matches "FirstName", "firstname", "First_Name", etc.
        report_field_normalized = normalize_field_name(report_field_name)
        for actual_field in first_record.keys():
            actual_field_normalized = normalize_field_name(actual_field)
            if actual_field_normalized == report_field_normalized:
                print(f"  🔄 [QUALITY REPORT] Mapped field '{report_field_name}' to '{actual_field}' in cleanup rules (normalized: '{report_field_normalized}')")
                return actual_field
        
        # No match found, return original
        return report_field_name
    
    # 1. Convert duplicates to deduplication_rules
    key_field_duplicates = duplicates.get("key_field_duplicates", {})
    exact_duplicates = duplicates.get("exact_duplicates", {})
    
    # Prefer key field duplicates (more specific), fall back to exact duplicates
    if key_field_duplicates.get("count", 0) > 0:
        fields_checked = key_field_duplicates.get("fields_checked", [])
        if fields_checked:
            # Map field names to actual data field names
            mapped_fields = [map_field_name(field) for field in fields_checked]
            extraction_rules["deduplication_rules"] = {
                "enabled": True,
                "fields": mapped_fields,
                "strategy": "keep_first"  # Always keep first occurrence
            }
    elif exact_duplicates.get("count", 0) > 0:
        # For exact duplicates, we need to deduplicate on all fields
        # Since we don't have field names here, we'll enable deduplication
        # but let the deduplication function handle it (it can work with all fields if fields list is empty/None)
        # Actually, looking at apply_deduplication_rules, it requires fields list
        # For exact duplicates without key fields, we'll skip deduplication_rules
        # as it's not practical without field names
        # Note: This is a limitation - exact duplicates without key fields won't be handled
        pass
    
    # 2. Convert case inconsistencies to data_cleanup_rules
    case_inconsistency_keys = [k for k in inconsistencies.keys() if "case_inconsistency" in k]
    for key in case_inconsistency_keys:
        report_field_name = key.replace("case_inconsistency_", "")
        # Map to actual field name in data
        field_name = map_field_name(report_field_name)
        # Normalize to title case (standard for names)
        extraction_rules["data_cleanup_rules"][field_name] = {
            "normalize_case": "title"
        }
    
    # 3. Convert format issues (emails) to data_cleanup_rules
    email_issue_keys = [k for k in format_issues.keys() if "invalid_emails" in k]
    for key in email_issue_keys:
        report_field_name = key.replace("invalid_emails_", "")
        # Map to actual field name in data
        field_name = map_field_name(report_field_name)
        # Email cleanup: strip and lowercase (handled by type: "email")
        # Only add if not already in data_cleanup_rules
        if field_name not in extraction_rules["data_cleanup_rules"]:
            extraction_rules["data_cleanup_rules"][field_name] = {
                "type": "email"
            }
        else:
            # Merge: add type to existing rules
            extraction_rules["data_cleanup_rules"][field_name]["type"] = "email"
    
    # 4. Convert format issues (dates) - Note: dates can't be auto-fixed, so we skip them
    
    # Clean up: remove data_cleanup_rules if empty
    if not extraction_rules["data_cleanup_rules"]:
        extraction_rules["data_cleanup_rules"] = None
    
    return extraction_rules

