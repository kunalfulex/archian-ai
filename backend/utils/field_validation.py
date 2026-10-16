"""Shared field-value validation helpers (used by migration + recovery classification)."""
from __future__ import annotations

import re
from typing import Any, Optional

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def is_valid_email(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    return bool(EMAIL_PATTERN.match(text))


def email_validation_message(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if is_valid_email(text):
        return None
    return f"Invalid email format: {text[:120]}"


def infer_source_data_issue(
    field_name: str,
    value: Any,
) -> Optional[str]:
    """Return a human-readable source-data issue for known field shapes."""
    name = (field_name or "").strip()
    if name.lower() == "email" or name.endswith("Email"):
        return email_validation_message(value)
    return None
