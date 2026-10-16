"""Structured join contract from classifier + PLAN_SCOPE fields."""
from __future__ import annotations

from typing import Any, Dict, Optional


def build_join_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins join endpoints and keys for gsheets_execute_operation (multi-source combine)."""
    from services.planning.plan_scope import read_plan_scope

    g = dict(goal or read_plan_scope(classification))
    extra = list(classification.get("planner_additional_sources") or [])
    return {
        "source_name": (classification.get("source_name") or "").strip() or None,
        "source_sheet_name": (classification.get("source_sheet_name") or "").strip() or None,
        "destination_name": (classification.get("destination_name") or "").strip() or None,
        "destination_sheet_name": (
            (classification.get("destination_sheet_name") or "").strip() or None
        ),
        "join_key": (classification.get("join_key") or g.get("join_key") or "").strip() or None,
        "additional_sources": extra,
        "operation": "join",
    }
