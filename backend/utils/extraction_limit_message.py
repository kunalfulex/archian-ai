"""User-facing copy when extraction is capped by plan / credit balance."""
from __future__ import annotations


def credit_cap_extraction_message(*, extracted_count: int, original_count: int) -> str:
    """Message for a successful partial extract (file larger than credit balance allows)."""
    return (
        f"Extracted {extracted_count} of {original_count} records "
        f"(plan credit limit). Add credits or upgrade to extract the rest."
    )


def credit_cap_applied(*, original_count: int, can_extract: int) -> bool:
    """True when the source had more records than the user can afford to extract."""
    try:
        return int(original_count) > int(can_extract)
    except (TypeError, ValueError):
        return False
