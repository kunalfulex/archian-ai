"""Single-path planner v2 detection — PLAN_SCOPE → plan → execution."""
from __future__ import annotations

from typing import Any, Dict

from services.planning.plan_scope import read_plan_scope


def classification_has_plan_scope(classification: Dict[str, Any]) -> bool:
    """
    True when this turn went through planner v2 (PLAN_SCOPE + plan assembly).

    Pipeline turns with scope or a draft/approved execution plan must not use
    legacy architect shortcuts, keyword join routing, or post-extract bypasses.
    """
    snap = dict(classification or {})
    if read_plan_scope(snap):
        return True
    ep = snap.get("execution_plan")
    if isinstance(ep, dict) and ep.get("steps"):
        return True
    if snap.get("plan_id"):
        return True
    status = (snap.get("plan_status") or "").lower()
    if status in (
        "draft",
        "awaiting_approval",
        "awaiting_clarification",
        "approved",
        "running",
    ):
        return True
    return False


def classification_has_binding_contract(classification: Dict[str, Any]) -> bool:
    """True when plan_contract source was chosen by binding (not pending keyword pick)."""
    from services.planning.binding.guards import (
        plan_contract_from_classification,
        source_resolved_by_binding,
    )

    contract = plan_contract_from_classification(classification)
    return bool(contract and source_resolved_by_binding(contract))
