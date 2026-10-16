"""Structured search contract from PLAN_SCOPE + classification."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.plan_scope import read_plan_scope


def build_search_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins search target and criteria hints for gsheets_execute_operation (search)."""
    g = dict(goal or read_plan_scope(classification))
    mi = dict(classification.get("migration_intent") or {})
    criteria = (
        (classification.get("search_criteria") or "").strip()
        or (g.get("search_criteria") or "").strip()
        or (mi.get("search_criteria") or "").strip()
        or None
    )
    materialize = g.get("search_materialize")
    if materialize is None:
        materialize = classification.get("search_materialize")
    return {
        "operation": "search",
        "source_name": (classification.get("source_name") or "").strip() or None,
        "source_sheet_name": (classification.get("source_sheet_name") or "").strip() or None,
        "search_criteria": criteria,
        "materialize": materialize if materialize is not None else "inline",
    }
