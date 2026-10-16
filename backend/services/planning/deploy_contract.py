"""Structured deploy contract from PLAN_SCOPE + classification."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.plan_scope import read_plan_scope


def build_deploy_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins source/destination orgs and deploy scope for metadata_deploy_salesforce."""
    g = dict(goal or read_plan_scope(classification))
    contract: Dict[str, Any] = {
        "source_name": (classification.get("source_name") or "").strip() or None,
        "destination_name": (classification.get("destination_name") or "").strip() or None,
        "source_connector": (classification.get("source") or "").strip() or None,
        "destination_connector": (classification.get("destination") or "").strip() or None,
        "operation": "deploy_schema",
    }
    ops = [str(o).lower() for o in (g.get("operations") or [])]
    if ops:
        contract["operations"] = ops
    src = (classification.get("plan_contract") or {}).get("source") or {}
    dest = (classification.get("plan_contract") or {}).get("destination") or {}
    if isinstance(src, dict) and src.get("entity_key"):
        contract["source_entity_key"] = src["entity_key"]
    if isinstance(dest, dict) and dest.get("entity_key"):
        contract["destination_entity_key"] = dest["entity_key"]
    mi = classification.get("migration_intent") or {}
    if isinstance(mi, dict):
        hints = mi.get("field_hints") or mi.get("selected_fields")
        if hints:
            contract["field_hints"] = list(hints)
    return {k: v for k, v in contract.items() if v is not None}
