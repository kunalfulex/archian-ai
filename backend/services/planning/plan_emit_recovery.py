"""Recover from empty plan at SSE emit when slots are satisfied but assembly produced 0 steps."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from services.planning.schemas import ExecutionPlan, merge_plan_into_classification


async def try_recover_empty_plan_at_emit(
    classification: Dict[str, Any],
    plan: Optional[ExecutionPlan],
    *,
    prompt: Optional[str] = None,
    user_id: Optional[str] = None,
) -> Tuple[Dict[str, Any], Optional[ExecutionPlan], bool]:
    """
    One-shot retry: sync create metadata, rebuild steps when op_requirements say ready.

    Returns (classification, plan, recovered).
    """
    from services.planning.create_metadata import sync_create_assembly_readiness
    from services.planning.clarification_sync import sync_plan_clarifications
    from services.planning.op_requirements import (
        assess_plan_requirements,
        read_plan_requirements,
        ready_to_build_plan,
    )
    from services.planning.plan_builder import build_plan_steps, finalize_plan_status
    from services.planning.plan_scope import read_plan_scope

    cls = dict(classification or {})
    goal = read_plan_scope(cls) or {}
    ep = plan.model_copy(deep=True) if plan else ExecutionPlan(steps=[])
    cls = sync_create_assembly_readiness(cls, goal, plan=ep)
    reqs = read_plan_requirements(cls, parsed_goal=goal)

    from services.planning.resource_catalog import coerce_planning_catalog

    catalog = coerce_planning_catalog(cls.get("_planning_catalog"), cls)

    if not ready_to_build_plan(cls, parsed_goal=goal, requirements=reqs):
        gaps, _ = assess_plan_requirements(cls, reqs, parsed_goal=goal)
        if gaps and user_id:
            ep = await sync_plan_clarifications(
                ep,
                cls,
                user_id=str(user_id),
                requirements=reqs,
                parsed_goal=goal,
                catalog=catalog,
                prompt=prompt,
                advance_one=True,
            )
            blocking = ep.blocking_clarifications()
            if blocking:
                cls["needs_clarification"] = True
                cls["plan_status"] = "awaiting_clarification"
                cls["clarifications"] = [c.model_dump(mode="json") for c in blocking]
                return cls, ep, True
        return classification, plan, False

    ep = build_plan_steps(cls, prompt=prompt, plan=ep)
    if not ep.steps:
        return classification, plan, False

    ep = finalize_plan_status(ep)
    cls = merge_plan_into_classification(cls, ep)
    cls["needs_clarification"] = False
    if (cls.get("plan_status") or "").lower() == "draft":
        cls["plan_status"] = "awaiting_approval"
    return cls, ep, True
