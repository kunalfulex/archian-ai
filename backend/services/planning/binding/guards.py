"""Gates for when plan binding is authoritative vs legacy entity_resolver."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.schemas import ExecutionPlan, PlanContract, PlanEntityRef


def plan_contract_from_classification(classification: Dict[str, Any]) -> Optional[PlanContract]:
    """Load PlanContract from classification (or execution_plan metadata)."""
    cls = dict(classification or {})
    raw = cls.get("plan_contract")
    if not raw:
        ep = cls.get("execution_plan")
        if isinstance(ep, dict):
            meta = ep.get("metadata") or {}
            raw = meta.get("plan_contract")
    if not raw:
        return None
    try:
        return PlanContract.model_validate(raw)
    except Exception:
        return None


def entity_side_resolved_by_binding(ref: Optional[PlanEntityRef]) -> bool:
    if not ref or not ref.is_resolved():
        return False
    return (ref.resolution_source or "") in BINDING_RESOLUTION_SOURCES


def binding_side_ref(
    classification: Dict[str, Any],
    role: str,
) -> Optional[PlanEntityRef]:
    contract = plan_contract_from_classification(classification)
    if not contract:
        return None
    return contract.source if role == "source" else contract.destination


def destination_resolved_by_binding(contract: Optional[PlanContract]) -> bool:
    if not contract or not contract.destination.is_resolved():
        return False
    return (contract.destination.resolution_source or "") in BINDING_RESOLUTION_SOURCES

BINDING_RESOLUTION_SOURCES = frozenset(
    {
        "goal_entity",  # legacy binding label
        "llm_bind",
        "plan_pinned",
        "user_explicit",
    }
)


def load_plan_contract(
    plan: Optional[ExecutionPlan],
    classification: Dict[str, Any],
) -> Optional[PlanContract]:
    raw = (plan.metadata or {}).get("plan_contract") if plan else None
    if not raw:
        raw = classification.get("plan_contract")
    if not raw:
        return None
    try:
        return PlanContract.model_validate(raw)
    except Exception:
        return None


def source_resolved_by_binding(contract: Optional[PlanContract]) -> bool:
    if not contract or not contract.source.is_resolved():
        return False
    return (contract.source.resolution_source or "") in BINDING_RESOLUTION_SOURCES


def should_skip_source_entity_resolver(contract: Optional[PlanContract]) -> bool:
    """True when source entity was chosen by binding or explicit user pin — do not re-pick."""
    return source_resolved_by_binding(contract)


def plan_binding_needed(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
    contract: Optional[PlanContract],
    *,
    prompt: str = "",
) -> bool:
    """True when run_plan_binding_pipeline should run for this turn."""
    route = (classification.get("plan_route") or "").strip()
    if route in ("info_path", "general_path"):
        return False

    from services.planning.op_requirements import (
        read_plan_requirements,
        requirements_need_dest_bind,
        requirements_need_source_bind,
    )

    reqs = read_plan_requirements(classification, parsed_goal=parsed_goal)
    need_source = requirements_need_source_bind(reqs, classification)
    need_dest = requirements_need_dest_bind(reqs)
    if not need_source and not need_dest:
        return False

    from services.planning.continuation_policy import should_skip_source_binding

    if need_source and should_skip_source_binding(classification, parsed_goal, prompt=prompt):
        if contract and contract.source.is_resolved():
            need_source = False

    if need_source and source_resolved_by_binding(contract):
        need_source = False
    if need_dest and destination_resolved_by_binding(contract):
        from services.planning.continuation_policy import (
            plan_contract_destination_stale_vs_classification,
        )

        if not plan_contract_destination_stale_vs_classification(classification, contract):
            need_dest = False
    if need_dest:
        from services.planning.continuation_policy import (
            plan_contract_destination_stale_vs_classification,
        )
        from services.planning.entity_pins import pin_destination_from_classification

        if (
            contract
            and plan_contract_destination_stale_vs_classification(classification, contract)
        ):
            need_dest = True
        elif pin_destination_from_classification(classification):
            need_dest = False

    return need_source or need_dest
