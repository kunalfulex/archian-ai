"""Pick representative sample rows for field mapping previews."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _filled_value_count(record: Dict[str, Any]) -> int:
    score = 0
    for v in record.values():
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if isinstance(v, (list, dict)) and not v:
            continue
        score += 1
    return score


def pick_most_complete_record(
    records: Any,
    *,
    preferred: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Return the record with the most non-empty fields for mapping sample values.

    Ties favor the first record in the list (stable). If `preferred` is provided
    and ties on score, keep preferred.
    """
    if isinstance(records, dict):
        return dict(records)
    if not isinstance(records, list) or not records:
        return {}

    best: Dict[str, Any] = {}
    best_score = -1
    for item in records:
        if not isinstance(item, dict):
            continue
        score = _filled_value_count(item)
        if score > best_score:
            best_score = score
            best = item
        elif score == best_score and preferred is item:
            best = item

    if preferred and isinstance(preferred, dict) and _filled_value_count(preferred) >= best_score:
        return dict(preferred)
    return dict(best) if best else {}
