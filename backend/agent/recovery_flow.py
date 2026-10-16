"""Recovery offer + resume helpers (Agent Runtime v2)."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from agent.observe import build_observation_from_batch
from agent.observation import ExecutionObservation
from agent.recovery_orchestrator import RecoveryOrchestrator
from agent.recovery_policy import (
    MAX_RECOVERY_ITERATIONS,
    should_offer_failure_summary,
    should_offer_write_recovery,
)
from agent.registry import default_registry
from agent.session import AgentSession

logger = logging.getLogger(__name__)


def last_migration_observation(session: AgentSession) -> Optional[ExecutionObservation]:
    for entry in reversed(session.execution_trace or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("tool_id") != "start_migration":
            continue
        try:
            return ExecutionObservation.model_validate(entry)
        except Exception:
            continue
    return None


async def _diagnose_with_tools(
    session: AgentSession,
    observation: ExecutionObservation,
) -> Tuple[Dict[str, Any], Optional[str]]:
    from services.migration.recovery_activity import emit_recovery_activity

    registry = default_registry()
    batch_id = observation.batch_id or (observation.failure_report.batch_id if observation.failure_report else None)
    analyze_args: Dict[str, Any] = {}
    if batch_id:
        analyze_args["batch_id"] = batch_id
    if observation.job_id:
        analyze_args["job_id"] = observation.job_id

    emit_recovery_activity("analyze_patterns", "Analyzing failure patterns", "running")
    analyze_visible: Dict[str, Any] = {}
    analyze_result = await registry.invoke("analyze_migration_failures", analyze_args, session)
    if analyze_result.ok and analyze_result.user_visible:
        analyze_visible = dict(analyze_result.user_visible)
        if analyze_result.observation:
            observation = analyze_result.observation
    emit_recovery_activity("analyze_patterns", "Analyzing failure patterns", "done")

    query_parts: List[str] = ["migration failure recovery"]
    report = observation.failure_report
    if report:
        if report.failed_fields:
            query_parts.append(f"fields: {', '.join(report.failed_fields[:6])}")
        if report.top_errors:
            query_parts.append(report.top_errors[0].message[:120])
    emit_recovery_activity("check_similar_fixes", "Checking similar fixes", "running")
    retrieve_result = await registry.invoke(
        "retrieve_similar_pipelines",
        {
            "query": " ".join(query_parts),
            "stage": "migration",
            "chunk_type": "migration_partial",
            "destination_type": (report.destination_type if report else None) or session.destination,
            "source_type": session.source,
            "caller": "recovery",
        },
        session,
    )
    similar_context = None
    if retrieve_result.ok:
        similar_context = (retrieve_result.user_visible or {}).get("learning_context")
    emit_recovery_activity("check_similar_fixes", "Checking similar fixes", "done")
    return analyze_visible, similar_context


async def load_recovery_classification(batch_id: str):
    from services.migration.recovery_activity import emit_recovery_activity
    from services.migration_records_service import MigrationRecordsService
    from services.planning.recovery_error_classifier import classify_failed_records

    emit_recovery_activity("classify_failures", "Classifying failed records", "running")
    rows = await MigrationRecordsService.get_failed_records_for_recovery(batch_id)
    summary = classify_failed_records(rows, batch_id=batch_id)
    detail = None
    if summary.total_failed:
        detail = f"{summary.total_failed} failed record{'s' if summary.total_failed != 1 else ''}"
    emit_recovery_activity("classify_failures", "Classifying failed records", "done", detail=detail)
    return summary


async def build_inform_only_proposal(
    session: AgentSession,
    observation: ExecutionObservation,
    classification_summary,
) -> Dict[str, Any]:
    """Rule-based failure summary when no rows are recoverable via re-map/retry."""
    from services.migration.recovery_activity import emit_recovery_activity
    from services.planning.recovery_planner import (
        _rule_based_recovery_plan,
        recovery_plan_to_decision,
        recovery_plan_to_execution_plan,
    )

    emit_recovery_activity("build_recovery_plan", "Building recovery plan", "running")

    recovery_plan = _rule_based_recovery_plan(
        observation,
        session=session,
        classification_summary=classification_summary,
    )
    decision = recovery_plan_to_decision(
        recovery_plan,
        job_id=observation.job_id or session.active_job_id,
    )
    exec_plan = recovery_plan_to_execution_plan(recovery_plan)

    report_dump = None
    if observation.failure_report:
        report_dump = observation.failure_report.model_dump(mode="json")
    classification_dump = (
        classification_summary.model_dump(mode="json") if classification_summary else None
    )
    emit_recovery_activity("build_recovery_plan", "Building recovery plan", "done")
    return {
        "failure_report": report_dump,
        "recovery_classification": classification_dump,
        "recoverable_count": 0,
        "unrecoverable_count": (
            classification_summary.unrecoverable_count if classification_summary else 0
        ),
        "recoverable_record_indices": [],
        "recovery_action": (
            classification_summary.recovery_action if classification_summary else "inform_only"
        ),
        "unrecoverable_notice": (
            classification_summary.unrecoverable_notice if classification_summary else ""
        ),
        "what_went_wrong": recovery_plan.what_went_wrong,
        "error_clusters": [
            {
                **c.model_dump(mode="json"),
                "resolvable": getattr(c, "resolvable", False),
            }
            for c in recovery_plan.error_clusters
        ],
        "relationship_notice": recovery_plan.relationship_notice,
        "narrative": recovery_plan.what_went_wrong,
        "failed_fields": (report_dump or {}).get("failed_fields") or [],
        "top_errors": [
            e.model_dump(mode="json") if hasattr(e, "model_dump") else e
            for e in (observation.failure_report.top_errors or [])
        ]
        if observation.failure_report
        else [],
        "similar_pipelines_context": None,
        "remediation_proposal": {},
        "decision": decision.model_dump(mode="json"),
        "requires_write": False,
        "recovery_execution_plan": exec_plan,
        "recovery_confidence": recovery_plan.confidence,
        "inform_only": True,
    }


async def build_recovery_proposal(
    session: AgentSession,
    observation: ExecutionObservation,
    *,
    classification_summary=None,
) -> Dict[str, Any]:
    batch_id = observation.batch_id or (
        observation.failure_report.batch_id if observation.failure_report else None
    )
    if classification_summary is None and batch_id:
        classification_summary = await load_recovery_classification(str(batch_id))

    analyze_visible, similar_context = await _diagnose_with_tools(session, observation)
    remediation = await default_registry().invoke(
        "propose_mapping_remediation",
        {
            "failure_report": (
                observation.failure_report.model_dump(mode="json")
                if observation.failure_report
                else None
            ),
            "batch_id": observation.batch_id,
        },
        session,
    )
    remediation_proposal = {}
    if remediation.ok and remediation.user_visible:
        remediation_proposal = remediation.user_visible.get("proposal") or {}

    from services.planning.recovery_planner import (
        plan_recovery_with_llm,
        recovery_plan_to_decision,
        recovery_plan_to_execution_plan,
    )
    from services.migration.recovery_activity import emit_recovery_activity

    emit_recovery_activity("build_recovery_plan", "Building recovery plan", "running")
    recovery_plan = await plan_recovery_with_llm(
        session,
        observation,
        analyze_visible=analyze_visible,
        remediation_proposal=remediation_proposal,
        similar_context=similar_context,
        classification_summary=classification_summary,
    )
    decision = recovery_plan_to_decision(
        recovery_plan,
        job_id=observation.job_id or session.active_job_id,
    )
    exec_plan = recovery_plan_to_execution_plan(recovery_plan)

    requires_write = bool(
        classification_summary
        and should_offer_write_recovery(
            observation,
            recoverable_count=classification_summary.recoverable_count,
        )
        and classification_summary.recovery_action == "remap_and_retry"
        and recovery_plan.steps
        and any(s.tool_id in ("map_fields", "start_migration") for s in recovery_plan.steps)
    )
    emit_recovery_activity("build_recovery_plan", "Building recovery plan", "done")

    report_dump = None
    if observation.failure_report:
        report_dump = observation.failure_report.model_dump(mode="json")
    classification_dump = (
        classification_summary.model_dump(mode="json") if classification_summary else None
    )
    return {
        "failure_report": report_dump,
        "recovery_classification": classification_dump,
        "recoverable_count": (
            classification_summary.recoverable_count if classification_summary else 0
        ),
        "unrecoverable_count": (
            classification_summary.unrecoverable_count if classification_summary else 0
        ),
        "recoverable_record_indices": (
            classification_summary.recoverable_record_indices if classification_summary else []
        ),
        "recovery_action": (
            classification_summary.recovery_action if classification_summary else "inform_only"
        ),
        "unrecoverable_notice": (
            classification_summary.unrecoverable_notice if classification_summary else ""
        ),
        "what_went_wrong": recovery_plan.what_went_wrong,
        "error_clusters": [
            {
                **c.model_dump(mode="json"),
                "resolvable": getattr(c, "resolvable", False),
            }
            for c in recovery_plan.error_clusters
        ],
        "relationship_notice": recovery_plan.relationship_notice,
        "narrative": recovery_plan.what_went_wrong or analyze_visible.get("narrative"),
        "failed_fields": analyze_visible.get("failed_fields") or (report_dump or {}).get("failed_fields") or [],
        "top_errors": analyze_visible.get("top_errors") or [],
        "similar_pipelines_context": similar_context,
        "remediation_proposal": remediation_proposal,
        "decision": decision.model_dump(mode="json"),
        "requires_write": requires_write,
        "recovery_execution_plan": exec_plan,
        "recovery_confidence": recovery_plan.confidence,
    }


async def refresh_observation_for_batch(
    batch_id: str,
    *,
    job_id: Optional[str] = None,
    mapping_id: Optional[str] = None,
) -> ExecutionObservation:
    return await build_observation_from_batch(
        tool_id="start_migration",
        batch_id=batch_id,
        job_id=job_id,
        mapping_id=mapping_id,
    )


def _recovery_iteration_exceeded(classification: Dict[str, Any]) -> bool:
    count = int(classification.get("recovery_iteration_count") or 0)
    return count >= MAX_RECOVERY_ITERATIONS


async def _load_classification_context(
    conversation_id: str,
    user_id: str,
    event_data: Dict[str, Any],
) -> Tuple[Dict[str, Any], Optional[str]]:
    original_prompt: Optional[str] = None
    classification: Optional[Dict[str, Any]] = None
    try:
        from services.chat_service import ChatService

        messages = await ChatService.get_messages(conversation_id, user_id, limit=120)
        for msg in reversed(messages or []):
            meta = msg.get("metadata") or {}
            pending = meta.get("planner_pending") or {}
            if isinstance(pending, dict) and pending.get("classification"):
                classification = dict(pending["classification"])
                original_prompt = pending.get("original_prompt") or meta.get("user_prompt")
                break
            if meta.get("event_type") == "planner_plan_ready":
                classification = {
                    "plan_id": meta.get("plan_id"),
                    "plan_status": meta.get("plan_status"),
                    "destination": event_data.get("destination"),
                    "destination_name": event_data.get("destination_name"),
                    "goal_summary": meta.get("goal_summary"),
                    "plan_steps_summary": meta.get("plan_steps_summary"),
                }
                original_prompt = meta.get("user_prompt")
                break
    except Exception as exc:
        logger.warning("recovery classification load failed: %s", exc)

    if not classification:
        classification = {
            "mode": "migrate",
            "destination": event_data.get("destination"),
            "destination_name": event_data.get("destination_name"),
            "source": event_data.get("source_type"),
            "active_job_id": event_data.get("source_job_id"),
        }
    if event_data.get("source_job_id"):
        classification["active_job_id"] = event_data["source_job_id"]
    if event_data.get("destination"):
        classification["destination"] = event_data["destination"]
    if event_data.get("destination_name"):
        classification["destination_name"] = event_data["destination_name"]
    return classification, original_prompt


async def maybe_offer_recovery_via_chat(
    *,
    user_id: str,
    conversation_id: str,
    batch_id: str,
    job_id: Optional[str] = None,
    event_data: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """After migration completes, persist a recovery panel message when warranted."""
    from services.migration.recovery_activity import emit_recovery_activity

    event_data = dict(event_data or {})
    emit_recovery_activity("review_results", "Reviewing migration results", "running")
    observation = await refresh_observation_for_batch(
        batch_id,
        job_id=job_id or event_data.get("job_id"),
    )
    failed_count = observation.failure_report.failed_count if observation.failure_report else 0
    review_detail = None
    if failed_count:
        review_detail = f"{failed_count} failed record{'s' if failed_count != 1 else ''}"
    emit_recovery_activity(
        "review_results",
        "Reviewing migration results",
        "done",
        detail=review_detail,
    )
    if not should_offer_failure_summary(observation):
        logger.info(
            "recovery skipped batch=%s status=%s rate=%s failed=%s",
            batch_id,
            (observation.metrics or {}).get("batch_status"),
            (observation.metrics or {}).get("success_rate"),
            (observation.failure_report.failed_count if observation.failure_report else 0),
        )
        return None

    classification, original_prompt = await _load_classification_context(
        conversation_id, user_id, event_data
    )
    if _recovery_iteration_exceeded(classification):
        return None

    classification_summary = await load_recovery_classification(str(batch_id))

    from agent.session import infer_agent_mode_from_classification

    session = AgentSession(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        mode=infer_agent_mode_from_classification(classification),
        prompt=(original_prompt or "").strip(),
        destination=classification.get("destination"),
        destination_name=classification.get("destination_name"),
        source=classification.get("source"),
        active_job_id=classification.get("active_job_id") or event_data.get("source_job_id"),
        classification_snapshot=classification,
    )

    if should_offer_write_recovery(
        observation,
        recoverable_count=classification_summary.recoverable_count,
    ):
        proposal = await build_recovery_proposal(
            session, observation, classification_summary=classification_summary
        )
    else:
        proposal = await build_inform_only_proposal(
            session, observation, classification_summary
        )

    from services.planning.plan_execution import build_recovery_pending

    pending = build_recovery_pending(
        classification=classification,
        original_prompt=original_prompt or "",
        recovery_proposal=proposal,
        batch_id=batch_id,
        job_id=job_id or event_data.get("job_id"),
        conversation_id=conversation_id,
    )

    message = _recovery_chat_message(proposal)
    emit_recovery_activity("recovery_ready", "Recovery plan ready", "running")
    try:
        from services.chat_service import ChatService
        from services.planning.chat_metadata import build_planner_chat_metadata

        await ChatService.add_message(
            conversation_id,
            str(user_id),
            "assistant",
            message,
            build_planner_chat_metadata(
                classification,
                event_type="planner_recovery_required",
                planner_pending=pending,
                extra={
                    "recovery_proposal": proposal,
                    "show_recovery_approval": bool(proposal.get("requires_write")),
                },
            ),
        )
    except Exception as exc:
        logger.warning("Failed to persist recovery offer message: %s", exc)
        emit_recovery_activity("recovery_ready", "Recovery plan ready", "failed")
        return None

    emit_recovery_activity("recovery_ready", "Recovery plan ready", "done")

    logger.info(
        "recovery offered batch=%s requires_write=%s conversation=%s",
        batch_id,
        bool(proposal.get("requires_write")),
        conversation_id,
    )

    try:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled():
            PipelineLearningService.schedule_capture(
                lambda: PipelineLearningService.capture_recovery_attempt(
                    user_id=str(user_id),
                    conversation_id=conversation_id,
                    batch_id=batch_id,
                    proposal=proposal,
                    outcome="offered",
                )
            )
    except Exception:
        pass

    return pending


def _recovery_chat_message(proposal: Dict[str, Any]) -> str:
    narrative = (proposal.get("narrative") or proposal.get("what_went_wrong") or "").strip()
    decision = proposal.get("decision") or {}
    rationale = (decision.get("rationale") or "").strip()
    if proposal.get("requires_write"):
        lines = ["**Migration recovery** — review suggested next steps.", ""]
    else:
        lines = ["**Migration completed with errors** — review what failed below.", ""]
    if narrative:
        lines.append(narrative)
        lines.append("")
    if rationale and rationale != narrative:
        lines.append(rationale)
        lines.append("")
    if proposal.get("requires_write"):
        lines.append("_Review the recovery plan below, then click **Apply fix** to re-map and retry migration._")
    elif (proposal.get("recovery_execution_plan") or {}).get("steps"):
        lines.append("_Review the recovery plan below. Automatic remediation needs your approval._")
    elif proposal.get("inform_only") or int(proposal.get("unrecoverable_count") or 0) > 0:
        lines.append("_These failures cannot be auto-fixed — correct source data or review records in View Results._")
    else:
        lines.append("_Review the errors above. Automatic remediation is not recommended._")
    return "\n".join(lines).strip()


async def maybe_offer_recovery_from_session(
    session: AgentSession,
) -> Optional[Dict[str, Any]]:
    """Runtime hook after plan steps when batch failure is already known.

    Skips background migrations — those are diagnosed only after
    migration_batches.status is terminal via maybe_offer_recovery_via_chat.
    """
    if last_migration_observation(session) is not None:
        return None

    observation = None
    if session.execution_trace:
        last = session.execution_trace[-1]
        if isinstance(last, dict) and last.get("batch_id"):
            observation = await refresh_observation_for_batch(
                str(last["batch_id"]),
                job_id=last.get("job_id") or session.active_job_id,
                mapping_id=last.get("mapping_id") or session.mapping_id,
            )
    if not observation or not should_offer_failure_summary(observation):
        return None

    snap = session.classification_snapshot or {}
    if _recovery_iteration_exceeded(snap):
        return None

    batch_id = observation.batch_id or (
        observation.failure_report.batch_id if observation.failure_report else None
    )
    classification_summary = None
    if batch_id:
        classification_summary = await load_recovery_classification(str(batch_id))

    if should_offer_write_recovery(
        observation,
        recoverable_count=(
            classification_summary.recoverable_count if classification_summary else 0
        ),
    ):
        proposal = await build_recovery_proposal(session, observation)
    else:
        proposal = await build_inform_only_proposal(
            session, observation, classification_summary
        )
    from services.planning.plan_execution import build_recovery_pending

    batch_id = observation.batch_id or (observation.failure_report.batch_id if observation.failure_report else None)
    return build_recovery_pending(
        classification=dict(snap),
        original_prompt=(session.prompt or "").strip(),
        recovery_proposal=proposal,
        batch_id=str(batch_id or ""),
        job_id=observation.job_id or session.active_job_id,
        conversation_id=session.conversation_id,
    )
