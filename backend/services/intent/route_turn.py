"""Route planner turns: fresh vs continuation vs general."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Literal, Optional

from services.intent.job_context import JobContext, load_job_context
from services.intent.session_continuation import (
    SessionContinuationDecision,
    session_action_is_fresh,
)
from services.planning.resource_catalog import ResourceCatalog

logger = logging.getLogger(__name__)

PlanningRoute = Literal["fresh_path", "continuation_path"]


async def route_planning_path(
    *,
    session_decision: SessionContinuationDecision,
    prompt: str,
    user_id: str,
    catalog: ResourceCatalog,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[str] = None,
    job_context: Optional[JobContext] = None,
    pre_classification: Optional[Dict[str, Any]] = None,
) -> PlanningRoute:
    """
    Mechanical routing after S01 — trusts session_action from the LLM decision.

    Semantic fresh vs continuation is decided in resolve_session_continuation (S01).
    General/info turns exit earlier in classify_and_enrich (Gate 0).
    """
    if session_action_is_fresh(session_decision.session_action):
        logger.info(
            "[plan_route] fresh_path session_action=%s source=%s",
            session_decision.session_action,
            session_decision.decision_source,
        )
        return "fresh_path"

    if session_decision.session_action == "no_prior_job":
        logger.info("[plan_route] fresh_path no_prior_job")
        return "fresh_path"

    if not session_decision.reuse_job_id or not session_decision.job_id:
        logger.info(
            "[plan_route] fresh_path no reuse job_id=%s reuse=%s",
            session_decision.job_id,
            session_decision.reuse_job_id,
        )
        return "fresh_path"

    job = job_context
    if job is None:
        job = await load_job_context(
            user_id=str(user_id),
            job_id=str(session_decision.job_id),
            conversation_id=conversation_id,
            conversation_history=conversation_history,
        )

    if not job:
        logger.debug(
            "[plan_route] fresh_path job_context missing job_id=%s",
            session_decision.job_id,
        )
        return "fresh_path"

    if session_decision.session_action != "continue_active_job":
        logger.debug(
            "[plan_route] fresh_path unexpected session_action=%s",
            session_decision.session_action,
        )
        return "fresh_path"

    if _prompt_names_different_source(
        prompt,
        job,
        catalog,
        session_decision=session_decision,
        pre_classification=pre_classification,
    ):
        logger.debug(
            "[plan_route] fresh_path different source in prompt job=%s",
            job.job_id,
        )
        return "fresh_path"

    logger.info(
        "[plan_route] continuation_path job_id=%s stage=%s mapping_ready=%s source=%s",
        job.job_id,
        job.pipeline_stage,
        job.mapping_ready,
        session_decision.decision_source,
    )
    return "continuation_path"


def _prompt_names_different_source(
    prompt: str,
    job: JobContext,
    catalog: ResourceCatalog,
    *,
    session_decision: Optional[SessionContinuationDecision] = None,
    pre_classification: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Safety net when S01 says continue but endpoint resolution names a new extract
    source. Destination overrides on continuation use the shared planning kernel.
    """
    from services.planning.endpoint_resolver import resolve_endpoints
    from services.planning.plan_scope import read_plan_scope

    pl = (prompt or "").strip()
    if not pl:
        return False

    phase = (
        (session_decision.continuation_phase if session_decision else None) or "none"
    ).lower()
    if phase in ("map", "migrate", "data_ops"):
        return False

    scope = read_plan_scope(pre_classification or {})
    scope_ops = [str(o).lower() for o in (scope.get("operations") or [])]
    if scope_ops and "extract" not in scope_ops:
        return False

    job_src = (job.source.display_name or "").strip()
    if not job_src:
        return False

    cls = pre_classification or {}
    cls_src = (cls.get("source_name") or "").strip()
    if cls_src and cls_src.lower() != job_src.lower():
        return True

    resolution = resolve_endpoints(pl, catalog, classification=cls)
    src = resolution.source
    if not src or not (src.display_name or "").strip():
        return False

    return src.display_name.strip().lower() != job_src.lower()
