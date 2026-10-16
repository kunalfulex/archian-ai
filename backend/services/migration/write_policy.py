"""Resolve migration write operation from mapping duplicate-check flags and destination."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class MigrationWritePolicy:
    operation: str
    reason: str


def mapping_has_duplicate_keys(mapping: Dict[str, Any]) -> bool:
    """True when any mapped field is marked is_duplicate_check (split or simple)."""
    for _field, info in (mapping or {}).items():
        if isinstance(info, list):
            if any(isinstance(item, dict) and item.get("is_duplicate_check") for item in info):
                return True
        elif isinstance(info, dict) and info.get("is_duplicate_check"):
            return True
    return False


# Backward-compatible alias
_mapping_has_duplicate_keys = mapping_has_duplicate_keys


def resolve_write_policy(
    *,
    destination: str,
    mapping: Optional[Dict[str, Any]] = None,
    explicit_operation: Optional[str] = None,
) -> MigrationWritePolicy:
    """
    Choose write mode from mapping duplicate-check fields and destination defaults.

    Duplicate-check columns always use upsert (query destination → update or insert)
    regardless of source/destination connector pairing.

    User/plan may set explicit_operation on MigrationContext; that always wins.
    """
    op = (explicit_operation or "").strip().lower()
    if op:
        return MigrationWritePolicy(operation=op, reason="explicit_operation")

    dest = (destination or "salesforce").strip().lower()
    if dest == "gsheets":
        dest = "google_sheets"

    if mapping_has_duplicate_keys(mapping or {}):
        return MigrationWritePolicy(
            operation="upsert",
            reason="mapping_duplicate_check",
        )

    if dest == "google_sheets":
        return MigrationWritePolicy(operation="append", reason="default_google_sheets")
    if dest == "sql":
        return MigrationWritePolicy(operation="insert", reason="default_sql")
    return MigrationWritePolicy(operation="upsert", reason="default_salesforce")
