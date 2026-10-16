"""Unified planner session loop — slots → clar OR build_plan_steps; one UI outcome per turn."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import ExecutionPlan, merge_plan_into_classification
from services.planning.plan_builder import finalize_plan_status
from services.planning.plan_context import pipeline_state_from_plan

logger = logging.getLogger(__name__)


class PlannerTurnKind(str, Enum):
    CLARIFICATION = "clarification"
    PLAN_READY = "plan_ready"
    EXECUTE = "execute"
    ERROR = "error"


@dataclass
class PlannerTurnOutcome:
    kind: PlannerTurnKind
    classification: Dict[str, Any]
    message: Optional[str] = None
    planner_pending: Optional[Dict[str, Any]] = None


def sync_classification_from_plan(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
) -> Dict[str, Any]:
    """Single source of truth for needs_clarification / plan_status from plan state."""
    out = dict(classification)
    blocking = plan.blocking_clarifications()
    if blocking:
        out["needs_clarification"] = True
        out["plan_status"] = "awaiting_clarification"
        out["clarifications"] = [c.model_dump(mode="json") for c in blocking]
    elif plan.steps:
        out["needs_clarification"] = False
        status = (plan.status or "draft").lower()
        out["plan_status"] = status if status in ("awaiting_approval", "approved", "running") else "awaiting_approval"
        out.pop("clarifications", None)
    else:
        out["needs_clarification"] = False
        out["plan_status"] = "draft"
        out.pop("clarifications", None)
    out["pipeline_state"] = pipeline_state_from_plan(
        plan.status,
        needs_clarification=bool(out.get("needs_clarification")),
    )
    return out


def classify_planner_turn_outcome(classification: Dict[str, Any]) -> PlannerTurnKind:
    """Map classification state to the SSE event kind for this planner turn."""
    if classification.get("needs_clarification"):
        return PlannerTurnKind.CLARIFICATION

    ep = classification.get("execution_plan") or {}
    steps = ep.get("steps") or [] if isinstance(ep, dict) else []
    status = (classification.get("plan_status") or ep.get("status") or "").lower()

    if not steps:
        return PlannerTurnKind.ERROR

    if status in ("approved", "running"):
        return PlannerTurnKind.EXECUTE

    return PlannerTurnKind.PLAN_READY


async def _ensure_blocking_clarification_composed(
    session,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
):
    """Compose LLM prompt when the blocking clar is still empty or <compose>."""
    from services.planning.clarification_compose import clarification_prompt_needs_compose
    from services.planning.clarification_pipeline import _compose_blocking_clarification

    blocking = session.plan.blocking_clarifications()
    if not blocking:
        return session
    if not clarification_prompt_needs_compose(blocking[0].prompt):
        return session
    session.plan = await _compose_blocking_clarification(
        session,
        user_id=str(user_id),
        conversation_id=conversation_id,
    )
    return session


async def _sync_requirement_gaps(
    session,
    *,
    user_id: str,
    catalog: Optional[ResourceCatalog] = None,
    conversation_id: Optional[str] = None,
) -> bool:
    """Rebuild open clarifications from slots; returns True if blocking."""
    if session.plan.blocking_clarifications():
        await _ensure_blocking_clarification_composed(
            session,
            user_id=str(user_id),
            conversation_id=conversation_id,
        )
        return True

    from services.planning.clarification_sync import sync_session_plan_clarifications
    from services.planning.plan_scope import read_plan_scope

    if catalog is not None:
        session.classification["_planning_catalog"] = catalog

    parsed_goal = session.parsed_goal or read_plan_scope(session.classification) or {}
    migration_intent = await _prepare_migration_intent_for_clarification_merge(
        session,
        user_id=str(user_id),
        parsed_goal=parsed_goal,
    )

    session = await sync_session_plan_clarifications(
        session,
        user_id=str(user_id),
        catalog=catalog,
        parsed_goal=parsed_goal,
        migration_intent=migration_intent,
        advance_one=True,
        conversation_id=conversation_id,
        compose=True,
    )
    return bool(session.plan.blocking_clarifications())


async def _prepare_migration_intent_for_clarification_merge(
    session,
    *,
    user_id: str,
    parsed_goal: Optional[Dict[str, Any]] = None,
    requirements=None,
) -> Any:
    """
    Ensure migration_intent has extract-field ambiguities before ranked-slot merge.

    Binding/pipeline may attach pick_extract_fields early; merge rebuilds from intent.
    """
    from services.planning.binding.guards import load_plan_contract
    from services.planning.clarification_queue import _extract_fields_slot_unsatisfied
    from services.planning.extract_field_clarification import resolve_field_clarification_for_plan
    from services.planning.migration_intent_schemas import PromptMigrationIntent
    from services.planning.op_requirements import read_plan_requirements
    from services.planning.plan_scope import read_plan_scope

    goal = parsed_goal or session.parsed_goal or read_plan_scope(session.classification) or {}
    reqs = requirements or read_plan_requirements(session.classification, parsed_goal=goal)
    plan_contract = load_plan_contract(session.plan, session.classification)

    raw_intent = (session.plan.metadata or {}).get("migration_intent") or session.classification.get(
        "migration_intent"
    )
    migration_intent = None
    if raw_intent:
        try:
            migration_intent = PromptMigrationIntent.model_validate(raw_intent)
        except Exception:
            migration_intent = None
    if migration_intent is None and getattr(session, "migration_intent", None) is not None:
        migration_intent = session.migration_intent

    if migration_intent is not None and migration_intent.needs_extract_clarification:
        if any(
            (a.ambiguity_type or "") == "pick_extract_fields"
            for a in (migration_intent.extract_ambiguities or [])
        ):
            return migration_intent

    if not _extract_fields_slot_unsatisfied(
        session.classification,
        reqs,
        plan_contract=plan_contract,
        parsed_goal=goal,
        migration_intent=migration_intent,
    ):
        return migration_intent

    resolved = await resolve_field_clarification_for_plan(
        migration_intent,
        user_id=str(user_id),
        classification=session.classification,
        prompt=session.prompt or "",
        plan_contract=plan_contract,
    )
    if resolved is None:
        return migration_intent

    session.migration_intent = resolved
    meta = dict(session.plan.metadata or {})
    meta["migration_intent"] = resolved.model_dump(mode="json")
    session.plan.metadata = meta
    return resolved


async def finalize_planner_session(
    session,
    *,
    user_id: str,
    catalog: ResourceCatalog,
    plan_auto_run: bool = False,
    conversation_id: Optional[str] = None,
) -> Any:
    """
    Loop until blocking clarification, plan steps, or exhausted retries.

    Invariant: never leave session with 0 steps, no blocking clar, and a satisfied slot set
    without at least attempting plan assembly.
    """
    from services.planning.clarification_pipeline import (
        PlannerSession,
        _run_plan_assembly_tail,
    )
    from services.planning.observability import log_planner_debug
    from services.planning.op_requirements import read_plan_requirements, ready_to_build_plan
    from services.planning.plan_scope import read_plan_scope

    session.classification["_pipeline_user_id"] = str(user_id)
    if conversation_id:
        session.classification["_conversation_id"] = conversation_id

    parsed_goal = session.parsed_goal or read_plan_scope(session.classification) or {}
    empty_assembly_iterations: List[int] = []

    for iteration in range(6):
        if session.plan.blocking_clarifications():
            await _ensure_blocking_clarification_composed(
                session,
                user_id=str(user_id),
                conversation_id=conversation_id,
            )
            break
        if session.plan.steps:
            break

        had_gaps = await _sync_requirement_gaps(
            session,
            user_id=str(user_id),
            catalog=catalog,
            conversation_id=conversation_id,
        )
        if had_gaps:
            break

        log_planner_debug(
            "planner_session_loop_assembly",
            user_id=str(user_id),
            conversation_id=conversation_id,
            classification=session.classification,
            plan=session.plan,
            extra={"iteration": iteration},
        )
        session = await _run_plan_assembly_tail(
            session,
            user_id=str(user_id),
            catalog=catalog,
            plan_auto_run=plan_auto_run,
            conversation_id=conversation_id,
        )

        if session.plan.steps or session.plan.blocking_clarifications():
            break

        reqs = read_plan_requirements(session.classification, parsed_goal=parsed_goal)
        from services.planning.binding.guards import load_plan_contract
        from services.planning.migration_intent_schemas import PromptMigrationIntent

        plan_contract = load_plan_contract(session.plan, session.classification)
        raw_intent = (session.plan.metadata or {}).get("migration_intent")
        migration_intent = None
        if raw_intent:
            try:
                migration_intent = PromptMigrationIntent.model_validate(raw_intent)
            except Exception:
                pass

        if ready_to_build_plan(
            session.classification,
            plan_contract=plan_contract,
            parsed_goal=parsed_goal,
            migration_intent=migration_intent,
            requirements=reqs,
        ):
            if not session.plan.steps:
                from services.planning.create_metadata import (
                    _goal_has_create,
                    spreadsheet_title_slot_satisfied,
                    sync_create_assembly_readiness,
                )

                synced = sync_create_assembly_readiness(
                    session.classification,
                    parsed_goal,
                    plan=session.plan,
                )
                session.classification = synced
                session = await _run_plan_assembly_tail(
                    session,
                    user_id=str(user_id),
                    catalog=catalog,
                    plan_auto_run=plan_auto_run,
                    conversation_id=conversation_id,
                )
                if session.plan.steps:
                    break
                tokens = list(reqs.pipeline_tokens or [])
                if "create" in tokens and _goal_has_create(parsed_goal) and not spreadsheet_title_slot_satisfied(
                    session.classification, parsed_goal
                ):
                    session.classification.pop("planner_spreadsheet_title_confirmed", None)
                    session.classification.pop("planner_destination_entity_confirmed", None)
                    logger.warning(
                        "[planner_session_loop] reopening create_title gap after 0-step assembly iteration=%s",
                        iteration,
                    )
                    continue
            empty_assembly_iterations.append(iteration)

    if (
        empty_assembly_iterations
        and not session.plan.steps
        and not session.plan.blocking_clarifications()
    ):
        from services.planning.observability import log_plan_empty_dead_end

        reqs = read_plan_requirements(session.classification, parsed_goal=parsed_goal)
        goal_ops = [
            str(o).lower()
            for o in (parsed_goal.get("operations") or [])
            if str(o).strip()
        ]
        log_plan_empty_dead_end(
            conversation_id=conversation_id,
            user_id=str(user_id),
            plan_id=session.plan.plan_id,
            pipeline_tokens=list(reqs.pipeline_tokens or []),
            goal_ops=goal_ops,
            gap_count=0,
            step_count=0,
            reason=(
                f"ready_to_build_plan but assembly produced 0 steps "
                f"(iterations={empty_assembly_iterations})"
            ),
        )

    if not session.plan.blocking_clarifications() and not session.plan.steps:
        await _sync_requirement_gaps(
            session,
            user_id=str(user_id),
            catalog=catalog,
            conversation_id=conversation_id,
        )

    session.plan = finalize_plan_status(session.plan, plan_auto_run=plan_auto_run)
    from services.planning.clarification_queue import merge_plan_clarification_queue_async
    from services.planning.op_requirements import read_plan_requirements

    reqs = read_plan_requirements(session.classification, parsed_goal=parsed_goal)
    from services.planning.resource_catalog import coerce_planning_catalog

    catalog_ref = coerce_planning_catalog(session.classification.get("_planning_catalog"), session.classification)

    migration_intent = await _prepare_migration_intent_for_clarification_merge(
        session,
        user_id=str(user_id),
        parsed_goal=parsed_goal,
        requirements=reqs,
    )
    session.plan = await merge_plan_clarification_queue_async(
        session.plan,
        session.classification,
        reqs,
        user_id=str(user_id),
        parsed_goal=parsed_goal,
        catalog=catalog_ref,
        prompt=session.prompt,
        migration_intent=migration_intent,
    )
    await _ensure_blocking_clarification_composed(
        session,
        user_id=str(user_id),
        conversation_id=conversation_id,
    )
    if session.plan.steps:
        from services.planning.binding.guards import load_plan_contract
        from services.planning.plan_builder import apply_plan_execution_summary

        plan_contract = load_plan_contract(session.plan, session.classification)
        session.plan = apply_plan_execution_summary(
            session.plan,
            migration_intent=migration_intent,
            plan_contract=plan_contract,
        )
    session.classification = merge_plan_into_classification(session.classification, session.plan)
    session.classification = sync_classification_from_plan(session.classification, session.plan)
    from services.planning.planning_checkpoint import persist_planning_checkpoint

    session.classification, session.plan = persist_planning_checkpoint(
        session.classification,
        session.plan,
        parsed_goal=parsed_goal,
    )
    if not session.classification.get("_plan_scope") and isinstance(
        (session.plan.metadata or {}).get("goal"), dict
    ):
        session.classification["_plan_scope"] = session.plan.metadata["goal"]

    log_planner_debug(
        "planner_session_loop_completed",
        user_id=str(user_id),
        conversation_id=conversation_id,
        classification=session.classification,
        plan=session.plan,
        extra={
            "step_count": len(session.plan.steps or []),
            "needs_clarification": session.classification.get("needs_clarification"),
            "outcome_kind": classify_planner_turn_outcome(session.classification).value,
            "plan_clar_types": [c.clarification_type for c in session.plan.clarifications],
        },
    )
    return session


def build_planner_turn_outcome(
    classification: Dict[str, Any],
    *,
    planner_pending: Optional[Dict[str, Any]] = None,
    default_error_message: Optional[str] = None,
) -> PlannerTurnOutcome:
    kind = classify_planner_turn_outcome(classification)
    message = None
    if kind == PlannerTurnKind.ERROR:
        message = (
            default_error_message
            or "Planning could not build an execution plan yet. "
            "Please complete any remaining setup steps or send your request again."
        )
    return PlannerTurnOutcome(
        kind=kind,
        classification=classification,
        message=message,
        planner_pending=planner_pending,
    )
