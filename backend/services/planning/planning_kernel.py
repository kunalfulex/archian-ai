"""Shared planning kernel — destination resolve → validate → pipeline → binding."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.intent.job_context import JobContext
from services.planning.clarification_pipeline import PlannerSession, advance_planner_pipeline
from services.planning.connector_guards import (
    classification_destination_endpoint_resolved,
    classification_source_endpoint_resolved,
)
from services.planning.resource_catalog import ResourceCatalog

logger = logging.getLogger(__name__)


def _job_summary_from_classification(classification: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "source_type": classification.get("source"),
        "source_name": classification.get("source_name"),
        "source_entity": (
            classification.get("source_object_api_name")
            or classification.get("source_table_name")
            or classification.get("source_entity_key")
        ),
        "prior_destination_type": classification.get("destination"),
        "prior_destination_name": classification.get("destination_name"),
    }


def should_skip_destination_llm(
    classification: Dict[str, Any],
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    job: Optional[JobContext] = None,
    plan_route: str = "",
    force_skip: bool = False,
) -> bool:
    """Do not re-run destination catalog LLM when endpoint is pinned or migrate-only inherit."""
    return destination_llm_skip_reason(
        classification,
        parsed_goal=parsed_goal,
        job=job,
        plan_route=plan_route,
        force_skip=force_skip,
    ) is not None


def destination_llm_skip_reason(
    classification: Dict[str, Any],
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    job: Optional[JobContext] = None,
    plan_route: str = "",
    force_skip: bool = False,
    catalog: Optional[ResourceCatalog] = None,
) -> Optional[str]:
    """Return skip reason when connector LLM should not run; None means run LLM."""
    if force_skip:
        return "force_skip"

    goal = dict(parsed_goal or classification.get("_plan_scope") or {})

    if (plan_route or classification.get("plan_route") or "").strip() == "continuation_path" and job:
        from services.planning.continuation_policy import (
            CONTINUATION_DEST_INHERIT,
            CONTINUATION_DEST_NONE,
            continuation_needs_destination_llm_resolution,
            resolve_continuation_destination_mode,
        )
        from services.planning.plan_scope import continuation_pipeline_tokens

        cont_steps = continuation_pipeline_tokens(classification, goal)
        dest_mode = resolve_continuation_destination_mode(
            cont_steps, goal, job, catalog=None
        )
        if dest_mode == CONTINUATION_DEST_NONE:
            return "no_map_step"
        if dest_mode == CONTINUATION_DEST_INHERIT:
            return "inherit_job_destination"
        if not continuation_needs_destination_llm_resolution(
            classification,
            goal,
            job,
            catalog=catalog,
            needs_connector_bind=True,
            prompt=classification.get("original_prompt"),
        ):
            if classification_destination_endpoint_resolved(classification):
                return "endpoint_resolved"
            return "no_destination_phrase"
        return None

    if classification.get("planner_pending") or classification.get("awaiting_clarification"):
        if classification_destination_endpoint_resolved(classification):
            return "clarification_connector_pinned"

    if classification_destination_endpoint_resolved(classification):
        dest_phrase = (goal.get("destination_phrase") or classification.get("destination_phrase") or "").strip()
        if not dest_phrase:
            return "endpoint_resolved"
        if job:
            from services.planning.continuation_policy import goal_requests_new_destination_endpoint

            if not goal_requests_new_destination_endpoint(goal, job):
                return "endpoint_matches_goal"
        return "endpoint_resolved"

    return None


def source_llm_skip_reason(
    classification: Dict[str, Any],
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    job: Optional[JobContext] = None,
    plan_route: str = "",
    force_skip: bool = False,
    catalog: Optional[ResourceCatalog] = None,
) -> Optional[str]:
    """Return skip reason when source catalog LLM should not run."""
    if force_skip:
        return "force_skip"

    from services.file_extraction.pending_upload import is_file_source

    if is_file_source(classification):
        return "file_upload"

    goal = dict(parsed_goal or classification.get("_plan_scope") or {})

    if (plan_route or classification.get("plan_route") or "").strip() == "continuation_path" and job:
        from services.planning.source_catalog_validate import read_needs_source

        if not read_needs_source(classification, goal):
            return "continuation_no_source_need"
        if classification_source_endpoint_resolved(
            classification, catalog=catalog, parsed_goal=goal
        ):
            return "endpoint_resolved"
        return None

    if classification.get("planner_pending") or classification.get("awaiting_clarification"):
        if classification_source_endpoint_resolved(
            classification, catalog=catalog, parsed_goal=goal
        ):
            return "clarification_connector_pinned"

    if classification_source_endpoint_resolved(
        classification, catalog=catalog, parsed_goal=goal
    ):
        source_phrase = (goal.get("source_phrase") or classification.get("source_phrase") or "").strip()
        if not source_phrase:
            return "endpoint_resolved"
        if job:
            from services.planning.continuation_policy import goal_requests_new_source_endpoint

            if not goal_requests_new_source_endpoint(goal, job):
                return "endpoint_matches_goal"
        return "endpoint_resolved"

    return None


async def resolve_source_connector(
    classification: Dict[str, Any],
    *,
    plan_route: str,
    prompt: str,
    parsed_goal: Dict[str, Any],
    catalog: ResourceCatalog,
    user_id: str,
    job: Optional[JobContext] = None,
    conversation_id: Optional[str] = None,
    force_skip: bool = False,
) -> Dict[str, Any]:
    """Connector-only source resolver (fresh extract / join)."""
    from services.planning.continuation_observability import log_continuation_bind

    skip_reason = source_llm_skip_reason(
        classification,
        parsed_goal=parsed_goal,
        job=job,
        plan_route=plan_route,
        force_skip=force_skip,
        catalog=catalog,
    )
    job_id = str(getattr(job, "job_id", "") or "") or None
    if skip_reason:
        if plan_route == "continuation_path":
            log_continuation_bind("source_connector", skipped=skip_reason, job_id=job_id)
        return classification

    goal = dict(parsed_goal or {})

    from services.planning.source_catalog_validate import (
        read_needs_source,
        source_connector_slot_satisfied,
        strip_unvalidated_source_confirm_flags,
    )
    from services.planning.source_llm_resolve import resolve_source_connector_via_llm

    out = strip_unvalidated_source_confirm_flags(dict(classification or {}))
    if not read_needs_source(out, goal):
        return out
    if source_connector_slot_satisfied(out, catalog, parsed_goal=goal):
        return out

    out["_source_connector_llm_attempted"] = True
    return await resolve_source_connector_via_llm(
        out,
        prompt=prompt,
        parsed_goal=goal,
        catalog=catalog,
        user_id=str(user_id),
        conversation_id=conversation_id,
        job=job,
    )


async def resolve_destination_connector(
    classification: Dict[str, Any],
    *,
    plan_route: str,
    prompt: str,
    parsed_goal: Dict[str, Any],
    catalog: ResourceCatalog,
    user_id: str,
    job: Optional[JobContext] = None,
    conversation_id: Optional[str] = None,
    force_skip: bool = False,
) -> Dict[str, Any]:
    """Connector-only destination resolver (continuation map/migrate + fresh)."""
    from services.planning.continuation_observability import log_continuation_bind

    skip_reason = destination_llm_skip_reason(
        classification,
        parsed_goal=parsed_goal,
        job=job,
        plan_route=plan_route,
        force_skip=force_skip,
        catalog=catalog,
    )
    job_id = str(getattr(job, "job_id", "") or "") or None
    if skip_reason:
        if plan_route == "continuation_path":
            log_continuation_bind("connector", skipped=skip_reason, job_id=job_id)
        return classification

    goal = dict(parsed_goal or {})

    if plan_route == "continuation_path" and job is not None:
        from services.planning.continuation_destination import (
            enrich_continuation_destination_connectors,
        )

        log_continuation_bind("connector", job_id=job_id)
        out = await enrich_continuation_destination_connectors(
            classification,
            prompt=prompt,
            catalog=catalog,
            goal=goal,
            job=job,
            user_id=str(user_id),
            conversation_id=conversation_id,
        )
        logger.info(
            "[planning_kernel] continuation destination type=%s name=%s",
            out.get("destination"),
            out.get("destination_name"),
        )
        return out

    from services.planning.destination_catalog_validate import (
        destination_connector_slot_satisfied,
        read_needs_destination,
        strip_unvalidated_destination_confirm_flags,
    )
    from services.planning.destination_llm_resolve import resolve_destination_connector_via_llm

    out = strip_unvalidated_destination_confirm_flags(dict(classification or {}))
    if not read_needs_destination(out, goal):
        return out
    if destination_connector_slot_satisfied(out, catalog, parsed_goal=goal):
        return out

    out["_destination_connector_llm_attempted"] = True
    return await resolve_destination_connector_via_llm(
        out,
        prompt=prompt,
        parsed_goal=goal,
        catalog=catalog,
        user_id=str(user_id),
        conversation_id=conversation_id,
        job=job,
    )


async def run_planning_kernel(
    session: PlannerSession,
    *,
    plan_route: str,
    user_id: str,
    catalog: ResourceCatalog,
    job: Optional[JobContext] = None,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[list] = None,
    plan_auto_run: bool = False,
    skip_destination_resolver: bool = False,
    skip_source_resolver: bool = False,
    run_destination_resolver: bool = True,
    run_source_resolver: bool = True,
) -> PlannerSession:
    """
    Shared kernel: source catalog LLM → destination catalog LLM → validate → advance_planner_pipeline.

    Fresh and continuation routes use the same binding/clarify/assemble tail.
    """
    del conversation_history
    from services.planning.planning_checkpoint import plan_scope_is_resolved, resolved_goal_for_session

    parsed_goal = resolved_goal_for_session(
        session.classification,
        parsed_goal=session.parsed_goal,
        plan=session.plan,
    )
    session.parsed_goal = parsed_goal
    job_id = str(getattr(job, "job_id", "") or "") or None

    session.classification["_planning_catalog"] = catalog
    from services.planning.destination_catalog_validate import (
        clear_destination_when_not_needed,
        strip_unvalidated_destination_confirm_flags,
    )
    from services.planning.source_catalog_validate import (
        clear_source_when_not_needed,
        strip_unvalidated_source_confirm_flags,
    )

    if plan_scope_is_resolved(parsed_goal):
        session.classification = clear_source_when_not_needed(session.classification, parsed_goal)
        session.classification = strip_unvalidated_source_confirm_flags(session.classification)
        session.classification = clear_destination_when_not_needed(
            session.classification, parsed_goal
        )
    else:
        session.classification = strip_unvalidated_source_confirm_flags(session.classification)
    session.classification = strip_unvalidated_destination_confirm_flags(session.classification)

    from services.planning.continuation_observability import log_continuation_bind

    if run_source_resolver:
        session.classification = await resolve_source_connector(
            session.classification,
            plan_route=plan_route,
            prompt=session.prompt or "",
            parsed_goal=parsed_goal,
            catalog=catalog,
            user_id=str(user_id),
            job=job,
            conversation_id=conversation_id,
            force_skip=skip_source_resolver,
        )
        parsed_goal = dict(session.parsed_goal or session.classification.get("_plan_scope") or parsed_goal)
        session.parsed_goal = parsed_goal
        session.classification["_plan_scope"] = parsed_goal
    elif plan_route == "continuation_path":
        log_continuation_bind("source_connector", skipped="resolver_disabled", job_id=job_id)

    if run_destination_resolver:
        session.classification = await resolve_destination_connector(
            session.classification,
            plan_route=plan_route,
            prompt=session.prompt or "",
            parsed_goal=parsed_goal,
            catalog=catalog,
            user_id=str(user_id),
            job=job,
            conversation_id=conversation_id,
            force_skip=skip_destination_resolver,
        )
        parsed_goal = dict(session.parsed_goal or session.classification.get("_plan_scope") or parsed_goal)
        session.parsed_goal = parsed_goal
        session.classification["_plan_scope"] = parsed_goal
    elif plan_route == "continuation_path":
        log_continuation_bind("connector", skipped="resolver_disabled", job_id=job_id)

    from services.planning.connector_guards import validate_classification_resources

    session.classification = validate_classification_resources(session.classification, catalog)

    if plan_route == "continuation_path" and job is not None:
        from services.intent.job_authority import apply_job_authority

        session.classification = apply_job_authority(session.classification, job)

    session = await advance_planner_pipeline(
        session,
        user_id=str(user_id),
        catalog=catalog,
        conversation_id=conversation_id,
        plan_auto_run=plan_auto_run,
    )
    return session
