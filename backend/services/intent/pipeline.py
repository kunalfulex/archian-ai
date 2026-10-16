"""Orchestrate intent pipeline steps S01–S12."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from services.intent.observability import StepTimer, log_intent_step
from services.intent.state import IntentState
from services.intent.steps.bind_resources import run_step_bind_resources
from services.intent.steps.context import run_step_context
from services.intent.steps.destination_need import run_step_destination_need
from services.intent.steps.entity import run_step_entity
from services.intent.steps.field_scope import run_step_field_scope
from services.intent.steps.gaps import run_step_gaps
from services.intent.steps.operations import run_step_operations
from services.intent.steps.plan_sketch import run_step_plan_sketch
from services.intent.steps.ready import run_step_ready
from services.intent.steps.system_types import run_step_system_types
from services.intent.steps.turn_type import run_step_turn_and_goal
from services.intent.types import IntentPipelineResult, IntentStepId, RouteTarget, StepResult
from services.planning.resource_catalog import ResourceCatalog


def _sync_from_classification(state: IntentState, classification: Dict[str, Any]) -> None:
    snap = dict(state.classifier_snapshot)
    for key in (
        "operations",
        "mode",
        "intent",
        "conversational_turn",
        "off_topic",
        "needs_clarification",
        "clarifications",
        "migration_intent",
        "plan_id",
        "plan_status",
        "planner_destination_confirmed",
        "planner_create_spreadsheet",
        "destination_phrase",
        "multi_source_extract",
        "session_action",
        "continuation_phase",
        "session_confidence",
        "active_job_id",
        "_continuation_steps",
        "plan_route",
        "_pipeline_tokens",
        "_binding_requirements",
        "_plan_scope",
    ):
        if classification.get(key) is not None:
            snap[key] = classification.get(key)
    state.classifier_snapshot = snap
    if classification.get("operations"):
        state.operations = [str(o).lower() for o in classification.get("operations") or []]
    state.mode = classification.get("mode") or state.mode
    if classification.get("plan_id"):
        state.plan_id = str(classification.get("plan_id"))
    if classification.get("plan_status"):
        state.plan_status = str(classification.get("plan_status"))
    steps = classification.get("plan_steps_summary") or []
    if isinstance(steps, list):
        state.plan_step_count = len(steps)


async def _run_steps(
    state: IntentState,
    step_runners: Sequence[tuple[IntentStepId, Any]],
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    stop_after: Optional[IntentStepId] = None,
    start_after: Optional[IntentStepId] = None,
) -> List[StepResult]:
    results: List[StepResult] = []
    stop = stop_after
    skipping = start_after is not None
    for step_id, runner in step_runners:
        if skipping:
            if step_id == start_after:
                skipping = False
            else:
                continue
        with StepTimer(
            step_id,
            state,
            user_id=user_id,
            conversation_id=conversation_id,
        ) as timer:
            result = await runner()
            timer.outcome = result.outcome
            timer.message = result.message
        results.append(result)
        if stop and step_id == stop:
            break
    return results


async def run_intent_pipeline(
    prompt: str,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    classification: Optional[Dict[str, Any]] = None,
    catalog: Optional[ResourceCatalog] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    intent_state_for_classifier: Optional[Dict[str, Any]] = None,
    phase: str = "pre_planning",
    start_after_step: Optional[IntentStepId] = None,
) -> IntentPipelineResult:
    """
    Run intent thinking steps for one user turn.

    phase=pre_planning: S01–S06 (before plan build)
    phase=post_planning: S07–S12 (after decomposer / entity resolver)
    phase=full: all steps in order
    """
    state = IntentState.from_prompt(
        prompt,
        user_id=user_id,
        conversation_id=conversation_id,
        classification=classification,
    )
    if classification:
        _sync_from_classification(state, classification)

    cls = classification or {}
    pre_steps = [
        (
            IntentStepId.S01_CONTEXT,
            lambda: run_step_context(
                state,
                conversation_history=conversation_history,
                intent_state_for_classifier=intent_state_for_classifier,
                classification=cls,
            ),
        ),
        (IntentStepId.S02_TURN_TYPE, lambda: run_step_turn_and_goal(state)),
        (IntentStepId.S04_DESTINATION_NEED, lambda: run_step_destination_need(state)),
        (
            IntentStepId.S06_BIND_RESOURCES,
            lambda: run_step_bind_resources(state, catalog=catalog or ResourceCatalog(), classification=cls),
        ),
        (IntentStepId.S05_SYSTEM_TYPES, lambda: run_step_system_types(state)),
    ]
    post_steps = [
        (
            IntentStepId.S07_ENTITY,
            lambda: run_step_entity(
                state,
                classification=cls,
                user_id=user_id,
                catalog=catalog,
            ),
        ),
        (IntentStepId.S08_FIELD_SCOPE, lambda: run_step_field_scope(state, classification=cls)),
        (IntentStepId.S09_OPERATIONS, lambda: run_step_operations(state, classification=cls)),
        (IntentStepId.S10_GAPS, lambda: run_step_gaps(
            state,
            classification=cls,
            catalog=catalog,
            user_id=user_id,
            prompt=prompt,
        )),
        (IntentStepId.S11_PLAN_SKETCH, lambda: run_step_plan_sketch(state, classification=cls)),
        (IntentStepId.S12_READY, lambda: run_step_ready(state, classification=cls)),
    ]

    if phase == "pre_planning":
        runners = pre_steps
        stop_after = IntentStepId.S05_SYSTEM_TYPES
    elif phase == "post_planning":
        runners = post_steps
        stop_after = IntentStepId.S12_READY
    else:
        runners = pre_steps + post_steps
        stop_after = IntentStepId.S12_READY

    if phase == "pre_planning" and not catalog:
        # S06 skipped when no catalog — still run S01-S05
        runners = [s for s in pre_steps if s[0] != IntentStepId.S06_BIND_RESOURCES]

    results = await _run_steps(
        state,
        runners,
        user_id=user_id,
        conversation_id=conversation_id,
        stop_after=stop_after,
        start_after=start_after_step,
    )

    route = RouteTarget.PLANNER
    if state.route == RouteTarget.GENERAL_CHAT.value:
        route = RouteTarget.GENERAL_CHAT
    elif state.route == RouteTarget.INFO_QUERY.value:
        route = RouteTarget.INFO_QUERY

    completed = [
        IntentStepId(s) for s in state.steps_completed if s in IntentStepId._value2member_map_
    ]

    from services.intent.metrics import metrics_from_state

    metrics = metrics_from_state(state, phase=phase)

    return IntentPipelineResult(
        state=state,
        steps_completed=completed,
        step_results=results,
        stopped_at=completed[-1] if completed else None,
        route=route,
        metrics=metrics,
    )
