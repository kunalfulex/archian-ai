"""Single writer for plan.clarifications — merge, enforce one blocking, compose."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional

from services.planning.schemas import ExecutionPlan
from services.planning.resource_catalog import ResourceCatalog

if TYPE_CHECKING:
    from services.planning.clarification_pipeline import PlannerSession


async def sync_plan_clarifications(
    plan: ExecutionPlan,
    classification: Dict[str, Any],
    *,
    user_id: str,
    requirements: Any,
    plan_contract: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    migration_intent: Any = None,
    catalog: Any = None,
    prompt: Optional[str] = None,
    advance_one: bool = False,
) -> ExecutionPlan:
    """
    Rebuild open clarifications from op_requirements + ranked builders.

    Sole materialization path for plan.clarifications during planning.
    """
    from services.planning.clarification_pipeline import (
        enforce_single_blocking_clarification,
    )
    from services.planning.clarification_queue import merge_plan_clarification_queue_async

    plan = await merge_plan_clarification_queue_async(
        plan,
        classification,
        requirements,
        user_id=str(user_id),
        plan_contract=plan_contract,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
        catalog=catalog,
        prompt=prompt,
    )
    if advance_one:
        plan = enforce_single_blocking_clarification(plan)
    return plan


async def sync_session_plan_clarifications(
    session: PlannerSession,
    *,
    user_id: str,
    catalog: Optional[ResourceCatalog] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    requirements: Any = None,
    migration_intent: Any = None,
    advance_one: bool = True,
    conversation_id: Optional[str] = None,
    compose: bool = True,
) -> PlannerSession:
    """Sync clarifications on a planner session; optionally compose blocking prompt."""
    from services.planning.binding.guards import load_plan_contract
    from services.planning.clarification_pipeline import (
        _compose_blocking_clarification,
        enforce_single_blocking_clarification,
    )
    from services.planning.op_requirements import read_plan_requirements
    from services.planning.plan_scope import read_plan_scope
    from services.planning.resource_catalog import coerce_planning_catalog

    goal = parsed_goal or session.parsed_goal or read_plan_scope(session.classification) or {}
    reqs = requirements or read_plan_requirements(session.classification, parsed_goal=goal)
    plan_contract = session.plan_contract or load_plan_contract(session.plan, session.classification)
    catalog_ref = coerce_planning_catalog(
        catalog or session.classification.get("_planning_catalog"),
        session.classification,
    )
    intent = migration_intent if migration_intent is not None else session.migration_intent
    if intent is None:
        raw = (session.plan.metadata or {}).get("migration_intent")
        if raw:
            from services.planning.migration_intent_schemas import PromptMigrationIntent

            try:
                intent = PromptMigrationIntent.model_validate(raw)
            except Exception:
                intent = None

    session.plan = await sync_plan_clarifications(
        session.plan,
        session.classification,
        user_id=str(user_id),
        requirements=reqs,
        plan_contract=plan_contract,
        parsed_goal=goal,
        migration_intent=intent,
        catalog=catalog_ref,
        prompt=session.prompt,
        advance_one=advance_one,
    )
    session.plan = enforce_single_blocking_clarification(session.plan)
    if compose and session.plan.blocking_clarifications():
        session.plan = await _compose_blocking_clarification(
            session,
            user_id=str(user_id),
            conversation_id=conversation_id,
        )
    return session
