"""Safe access for databases.Record rows (no .get — use dict(row) or __getitem__)."""
from __future__ import annotations

from typing import Any, Optional


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except (KeyError, TypeError):
        return default
