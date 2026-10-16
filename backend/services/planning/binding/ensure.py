"""Idempotent plan binding before assembly / clarification stages."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from services.planning.binding.guards import load_plan_contract, plan_binding_needed
from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, PlanContract


async def ensure_plan_binding_if_needed(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
    *,
    parsed_goal: Dict[str, Any],
    user_id: str,
    prompt: str,
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List] = None,
    migration_intent: Any = None,
) -> Tuple[Dict[str, Any], ExecutionPlan, Any, Optional[PlanContract]]:
    """
    Run PLAN_BINDING when source entity is not yet bound.

    Safe to call on fresh orchestrator path, clarify-resume, and legacy tail.
    """
    contract = load_plan_contract(plan, classification)
    if not plan_binding_needed(classification, parsed_goal, contract, prompt=prompt):
        return classification, plan, migration_intent, contract

    from services.planning.binding.pipeline import run_plan_binding_pipeline

    return await run_plan_binding_pipeline(
        classification,
        plan,
        user_id=str(user_id),
        prompt=prompt,
        parsed_goal=parsed_goal,
        catalog=catalog,
        conversation_id=conversation_id,
        conversation_history=conversation_history,
    )
