"""Shared helpers for Google Sheets mapping and transform resolution."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _norm_sheet(name: str) -> str:
    return (name or "").strip().lower()


def resolve_gsheets_primary_sheet(
    sheets_structure: Dict[str, Any],
    *,
    destination_sheet_name: Optional[str] = None,
) -> Optional[str]:
    """
    Canonical primary sheet for map + transform (single source of truth).

    Prefer explicit destination sheet when present in structure; otherwise the tab
    with the most columns (same heuristic as the mapping LLM prompt).
    """
    if not sheets_structure:
        return (destination_sheet_name or "").strip() or None

    if destination_sheet_name:
        dest = destination_sheet_name.strip()
        if dest in sheets_structure:
            return dest
        for key in sheets_structure:
            if _norm_sheet(key) == _norm_sheet(dest):
                return key

    return max(
        sheets_structure.items(),
        key=lambda item: len((item[1] or {}).get("columns") or {}),
    )[0]
