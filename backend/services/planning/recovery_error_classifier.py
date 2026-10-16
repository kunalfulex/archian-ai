"""Rule-first per-record migration failure classification for recovery policy."""
from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Optional, Pattern, Tuple

from pydantic import BaseModel, Field

from utils.field_validation import infer_source_data_issue

# --- Recovery error taxonomy (merged P4.9) ---


class RecoveryErrorCategory(str, Enum):
    """Per-record failure category for migration recovery policy."""

    SYSTEM_LIMIT = "system_limit"
    INVALID_SOURCE_DATA = "invalid_source_data"
    LOOKUP_RECOVERABLE = "lookup_recoverable"
    MAPPING_RECOVERABLE = "mapping_recoverable"
    UNKNOWN = "unknown"


RECOVERABLE_CATEGORIES: FrozenSet[RecoveryErrorCategory] = frozenset(
    {
        RecoveryErrorCategory.LOOKUP_RECOVERABLE,
        RecoveryErrorCategory.MAPPING_RECOVERABLE,
    }
)

INFORM_ONLY_CATEGORIES: FrozenSet[RecoveryErrorCategory] = frozenset(
    {
        RecoveryErrorCategory.SYSTEM_LIMIT,
        RecoveryErrorCategory.INVALID_SOURCE_DATA,
        RecoveryErrorCategory.UNKNOWN,
    }
)


def _compile_patterns(patterns: Tuple[str, ...]) -> Tuple[Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


SYSTEM_LIMIT_MESSAGE_PATTERNS: Tuple[Pattern[str], ...] = _compile_patterns(
    (
        r"STORAGE_LIMIT",
        r"LICENSE",
        r"ORG_LIMIT",
        r"LIMIT_EXCEEDED",
        r"GOVERNOR",
        r"INSUFFICIENT_ACCESS",
        r"INSUFFICIENT_ACCESS_OR_READONLY",
        r"FIELD_INTEGRITY_EXCEPTION.*storage",
        r"CANNOT_INSERT_UPDATE_ACTIVATE_ENTITY.*limit",
    )
)

LOOKUP_MESSAGE_PATTERNS: Tuple[Pattern[str], ...] = _compile_patterns(
    (
        r"foreign key",
        r"lookup",
        r"could not find.*record",
        r"INVALID_CROSS_REFERENCE",
        r"INVALID_ID_FIELD",
        r"FIELD_INTEGRITY_EXCEPTION.*foreign",
        r"REQUIRED_FIELD_MISSING.*AccountId",
        r"AccountId",
    )
)

MAPPING_MESSAGE_PATTERNS: Tuple[Pattern[str], ...] = _compile_patterns(
    (
        r"REQUIRED_FIELD_MISSING",
        r"Required fields? (?:are )?missing",
        r"INVALID_FIELD_FOR_INSERT_UPDATE",
        r"INVALID_TYPE_ON_FIELD",
        r"STRING_TOO_LONG",
        r"NUMBER_OUTSIDE_VALID_RANGE",
        r"bad value for restricted picklist",
        r"INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST",
    )
)

INVALID_SOURCE_DATA_PATTERNS: Tuple[Pattern[str], ...] = _compile_patterns(
    (
        r"invalid email",
        r"malformed email",
        r"INVALID_EMAIL_ADDRESS",
    )
)

BULK_MATCH_SYMPTOM_PATTERNS: Tuple[Pattern[str], ...] = _compile_patterns(
    (
        r"could not be matched to bulk",
        r"Record could not be matched",
    )
)

SOURCE_DATA_FIELD_HINTS: FrozenSet[str] = frozenset(
    {"Email", "email", "Phone", "MobilePhone", "Fax", "Website", "URL"}
)


def message_matches_any(message: str, patterns: Tuple[Pattern[str], ...]) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return any(p.search(text) for p in patterns)


def category_display_theme(category: RecoveryErrorCategory) -> str:
    """User-facing cluster theme from category."""
    return {
        RecoveryErrorCategory.SYSTEM_LIMIT: "System or org limit",
        RecoveryErrorCategory.INVALID_SOURCE_DATA: "Invalid source data",
        RecoveryErrorCategory.LOOKUP_RECOVERABLE: "Lookup / relationship",
        RecoveryErrorCategory.MAPPING_RECOVERABLE: "Field mapping",
        RecoveryErrorCategory.UNKNOWN: "Other errors",
    }.get(category, "Errors")


class ClassifiedFailedRecord(BaseModel):
    record_index: int
    error_message: str = ""
    category: RecoveryErrorCategory = RecoveryErrorCategory.UNKNOWN
    recoverable: bool = False
    theme: str = ""
    source_field_hint: Optional[str] = None
    detail: Optional[str] = None


class RecoveryClassificationSummary(BaseModel):
    batch_id: str
    total_failed: int = 0
    recoverable_count: int = 0
    unrecoverable_count: int = 0
    recoverable_record_indices: List[int] = Field(default_factory=list)
    records: List[ClassifiedFailedRecord] = Field(default_factory=list)
    clusters: List[Dict[str, Any]] = Field(default_factory=list)
    recovery_action: str = "inform_only"  # inform_only | remap_and_retry
    unrecoverable_notice: str = ""


def _parse_json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _messages_from_row(row: Dict[str, Any]) -> List[str]:
    messages: List[str] = []
    primary = (row.get("error_message") or "").strip()
    if primary:
        messages.append(primary)

    sf_results = row.get("salesforce_results")
    if isinstance(sf_results, str):
        try:
            sf_results = json.loads(sf_results)
        except (json.JSONDecodeError, TypeError):
            sf_results = {}
    if isinstance(sf_results, dict):
        for val in sf_results.values():
            if isinstance(val, dict):
                msg = (val.get("message") or "").strip()
                if msg and msg not in messages:
                    messages.append(msg)
            elif isinstance(val, str) and val.strip() and val not in messages:
                messages.append(val.strip())
    return messages


def _source_data_issues(source_data: Dict[str, Any]) -> List[tuple[str, str]]:
    issues: List[tuple[str, str]] = []
    for field, value in (source_data or {}).items():
        if field.startswith("_"):
            continue
        if field not in SOURCE_DATA_FIELD_HINTS and field.lower() not in {
            f.lower() for f in SOURCE_DATA_FIELD_HINTS
        }:
            if field.lower() != "email":
                continue
        issue = infer_source_data_issue(field, value)
        if issue:
            issues.append((field, issue))
    return issues


def classify_failed_record(
    *,
    record_index: int,
    error_message: str = "",
    source_data: Optional[Dict[str, Any]] = None,
    mapped_data: Optional[Dict[str, Any]] = None,
    salesforce_results: Optional[Any] = None,
) -> ClassifiedFailedRecord:
    """Classify a single failed migration record using rules + payload inspection."""
    row = {
        "error_message": error_message,
        "source_data": source_data or {},
        "mapped_data": mapped_data or {},
        "salesforce_results": salesforce_results,
    }
    messages = _messages_from_row(row)
    combined = " | ".join(messages)
    src = _parse_json_dict(source_data)
    source_issues = _source_data_issues(src)

    category = RecoveryErrorCategory.UNKNOWN
    detail: Optional[str] = None
    source_field_hint: Optional[str] = None

    if message_matches_any(combined, SYSTEM_LIMIT_MESSAGE_PATTERNS):
        category = RecoveryErrorCategory.SYSTEM_LIMIT
    elif message_matches_any(combined, LOOKUP_MESSAGE_PATTERNS):
        category = RecoveryErrorCategory.LOOKUP_RECOVERABLE
    elif message_matches_any(combined, MAPPING_MESSAGE_PATTERNS):
        category = RecoveryErrorCategory.MAPPING_RECOVERABLE
    elif message_matches_any(combined, INVALID_SOURCE_DATA_PATTERNS) or source_issues:
        category = RecoveryErrorCategory.INVALID_SOURCE_DATA
        if source_issues:
            source_field_hint, detail = source_issues[0]
    elif message_matches_any(combined, BULK_MATCH_SYMPTOM_PATTERNS):
        if source_issues:
            category = RecoveryErrorCategory.INVALID_SOURCE_DATA
            source_field_hint, detail = source_issues[0]
        else:
            category = RecoveryErrorCategory.LOOKUP_RECOVERABLE
            detail = combined[:240] if combined else None
    elif source_issues:
        category = RecoveryErrorCategory.INVALID_SOURCE_DATA
        source_field_hint, detail = source_issues[0]

    recoverable = category in RECOVERABLE_CATEGORIES
    theme = category_display_theme(category)
    return ClassifiedFailedRecord(
        record_index=int(record_index),
        error_message=(messages[0] if messages else (error_message or "")),
        category=category,
        recoverable=recoverable,
        theme=theme,
        source_field_hint=source_field_hint,
        detail=detail or (messages[0][:240] if messages else None),
    )


def classify_failed_records(rows: List[Dict[str, Any]], *, batch_id: str) -> RecoveryClassificationSummary:
    """Classify all failed rows for a batch and build cluster summary."""
    classified: List[ClassifiedFailedRecord] = []
    for row in rows:
        idx = row.get("record_index")
        if idx is None:
            continue
        classified.append(
            classify_failed_record(
                record_index=int(idx),
                error_message=str(row.get("error_message") or ""),
                source_data=_parse_json_dict(row.get("source_data")),
                mapped_data=_parse_json_dict(row.get("mapped_data")),
                salesforce_results=row.get("salesforce_results"),
            )
        )

    recoverable_indices = sorted({r.record_index for r in classified if r.recoverable})
    unrecoverable_count = sum(1 for r in classified if not r.recoverable)
    recoverable_count = len(recoverable_indices)

    cluster_map: Dict[tuple[str, bool], Dict[str, Any]] = {}
    for rec in classified:
        key = (rec.theme, rec.recoverable)
        if key not in cluster_map:
            cluster_map[key] = {
                "theme": rec.theme,
                "category": rec.category.value,
                "resolvable": rec.recoverable,
                "count": 0,
                "sample_message": rec.detail or rec.error_message,
                "fields": [],
            }
        cluster_map[key]["count"] += 1
        if rec.source_field_hint and rec.source_field_hint not in cluster_map[key]["fields"]:
            cluster_map[key]["fields"].append(rec.source_field_hint)

    clusters = sorted(cluster_map.values(), key=lambda c: (-c["count"], c["theme"]))
    action = "remap_and_retry" if recoverable_count > 0 else "inform_only"

    notice_parts: List[str] = []
    for cluster in clusters:
        if not cluster.get("resolvable"):
            notice_parts.append(f"{cluster['count']} — {cluster['theme']}")

    return RecoveryClassificationSummary(
        batch_id=batch_id,
        total_failed=len(classified),
        recoverable_count=recoverable_count,
        unrecoverable_count=unrecoverable_count,
        recoverable_record_indices=recoverable_indices,
        records=classified,
        clusters=clusters,
        recovery_action=action,
        unrecoverable_notice="; ".join(notice_parts[:6]),
    )
