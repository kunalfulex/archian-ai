"""Single entry: route prompt → S01 → planning enrichment."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from services.intent.classify_context import load_classify_context
from services.intent.routing_context import session_action_is_continue
from services.intent.enrich import apply_post_classification_pipeline
from services.intent.session_continuation import (
    merge_session_decision_into_classification,
    resolve_session_continuation,
)
from services.intent.session_state import get_active_intent_state
from services.planning.conversation_session import latest_extraction_session_for_classifier

logger = logging.getLogger(__name__)


def _classification_error_fallback(*, reason: str = "Unable to classify request") -> Dict[str, Any]:
    return {
        "source": "unknown",
        "destination": "unknown",
        "intent": "general",
        "source_name": None,
        "destination_name": None,
        "confidence": 0.0,
        "summary": reason,
    }


def _bootstrap_fresh_classification(
    *,
    prompt: str,
    session_decision: Any,
) -> Dict[str, Any]:
    """Minimal classification stub from S01 — PLAN_SCOPE owns scope and connector phrases."""
    out: Dict[str, Any] = {"original_prompt": prompt}
    return merge_session_decision_into_classification(out, session_decision)


def _resolve_job_id_hint(
    *,
    conversation_history: Optional[list],
    intent_state: Optional[Dict[str, Any]],
    latest_session: Optional[Dict[str, Any]],
) -> Optional[str]:
    from services.intent.session_continuation import _active_job_summary

    job_summary = _active_job_summary(conversation_history, intent_state)
    if job_summary and job_summary.get("job_id"):
        return str(job_summary["job_id"])
    for src in (intent_state, latest_session):
        if not src:
            continue
        for key in ("active_job_id", "job_id"):
            val = src.get(key)
            if val:
                return str(val)
    return None


async def classify_and_enrich(
    prompt: str,
    user_id: str,
    conversation_history: Optional[list] = None,
    conversation_id: Optional[str] = None,
    explicit_source: Optional[str] = None,
    explicit_source_name: Optional[str] = None,
    plan_auto_run: bool = False,
    gate0_decision=None,
) -> Dict[str, Any]:
    """
    Classify a user turn and run full post-classification enrichment.

    Routes: info_path, general_path (early), continuation_path, or fresh_path.

    When ``gate0_decision`` is provided (architect entry), Gate 0 is not run again.
    Planning continuation (clar answer in chat) runs after Gate 0 on the pipeline branch.
    """
    try:
        ctx = await load_classify_context(str(user_id))

        from services.intent.conversation_turn import enrich_general_turn, enrich_info_turn
        from services.intent.turn_type_routing import resolve_turn_type_routing

        if gate0_decision is not None:
            gate0 = gate0_decision
        else:
            gate0 = await resolve_turn_type_routing(
                prompt,
                user_id=str(user_id),
                conversation_id=str(conversation_id) if conversation_id else None,
                conversation_history=conversation_history,
            )
        if gate0.plan_route == "info_path":
            logger.info(
                "[classify_and_enrich] gate0=info_path conf=%.2f source=%s",
                gate0.confidence,
                gate0.decision_source,
            )
            return await enrich_info_turn(
                prompt,
                user_id=str(user_id),
                catalog=ctx.catalog,
                conversation_id=str(conversation_id) if conversation_id else None,
            )
        if gate0.plan_route == "general_path":
            logger.info(
                "[classify_and_enrich] gate0=general_path conf=%.2f source=%s",
                gate0.confidence,
                gate0.decision_source,
            )
            return await enrich_general_turn(
                prompt,
                user_id=str(user_id),
                conversation_id=str(conversation_id) if conversation_id else None,
                conversational_turn=gate0.conversational_turn,
                off_topic=gate0.off_topic,
            )

        logger.info(
            "[classify_and_enrich] gate0=pipeline conf=%.2f source=%s",
            gate0.confidence,
            gate0.decision_source,
        )

        from services.file_extraction.pending_upload import (
            attached_file_forces_fresh_session,
            fresh_session_decision_for_attached_file,
        )
        from services.intent.pipeline_turn import (
            continue_planning_from_prompt,
            merge_fresh_supersede_into_classification,
            supersede_pending_for_fresh_turn,
        )
        from services.intent.pipeline_turn_routing import resolve_pipeline_turn

        attached_file = attached_file_forces_fresh_session(explicit_source)
        fresh_supersede_early = None

        if attached_file:
            fresh_supersede_early = supersede_pending_for_fresh_turn(conversation_history)
            logger.info(
                "[classify_and_enrich] attached_file=%s skipping pipeline_turn continuation branches",
                (explicit_source_name or "upload").strip(),
            )
        else:
            pipeline_decision = await resolve_pipeline_turn(
                prompt,
                user_id=str(user_id),
                conversation_id=str(conversation_id) if conversation_id else None,
                conversation_history=conversation_history,
            )
            logger.info(
                "[classify_and_enrich] pipeline_turn branch=%s source=%s conversation_id=%s",
                pipeline_decision.branch,
                pipeline_decision.decision_source,
                conversation_id,
            )

            if pipeline_decision.branch == "fresh":
                fresh_supersede_early = supersede_pending_for_fresh_turn(conversation_history)

            if pipeline_decision.branch == "planning_continuation":
                continued = await continue_planning_from_prompt(
                    prompt,
                    conversation_history,
                    user_id=str(user_id),
                    conversation_id=str(conversation_id) if conversation_id else None,
                    plan_auto_run=plan_auto_run,
                    skip_reply_routing=False,
                )
                if continued is not None:
                    logger.info(
                        "[classify_and_enrich] continuation_path=planning_clar conversation_id=%s",
                        conversation_id,
                    )
                    return merge_fresh_supersede_into_classification(
                        continued,
                        fresh_supersede_early,
                    )

            if pipeline_decision.branch == "job_continuation":
                from services.intent.continuation_planning import plan_from_job_context
                from services.intent.job_context import load_job_context
                from services.intent.session_continuation import SessionContinuationDecision

                latest_session = latest_extraction_session_for_classifier(conversation_history)
                intent_state = await get_active_intent_state(
                    conversation_id,
                    user_id,
                    conversation_history,
                    job_id_hint=_resolve_job_id_hint(
                        conversation_history=conversation_history,
                        intent_state=None,
                        latest_session=latest_session,
                    ),
                )
                job_id_hint = _resolve_job_id_hint(
                    conversation_history=conversation_history,
                    intent_state=intent_state or latest_session,
                    latest_session=latest_session,
                )
                if job_id_hint:
                    job_context = await load_job_context(
                        user_id=str(user_id),
                        job_id=job_id_hint,
                        conversation_id=str(conversation_id) if conversation_id else None,
                        conversation_history=conversation_history,
                    )
                    session_decision = SessionContinuationDecision(
                        session_action="continue_active_job",
                        reuse_job_id=True,
                        job_id=job_id_hint,
                        confidence=pipeline_decision.confidence,
                        reasoning_short=pipeline_decision.reasoning_short,
                        decision_source="llm",
                    )
                    logger.info(
                        "[classify_and_enrich] continuation_path=job job_id=%s conversation_id=%s",
                        job_id_hint,
                        conversation_id,
                    )
                    return await plan_from_job_context(
                        user_id=str(user_id),
                        prompt=prompt,
                        session_decision=session_decision,
                        job=job_context,
                        catalog=ctx.catalog,
                        conversation_id=str(conversation_id) if conversation_id else None,
                        conversation_history=conversation_history,
                        plan_auto_run=plan_auto_run,
                    )

        intent_state = None
        latest_session = None
        job_context = None

        if attached_file:
            session_decision = fresh_session_decision_for_attached_file()
            logger.info(
                "[classify_and_enrich] attached_file guard action=%s (skipping S01 continuation)",
                session_decision.session_action,
            )
        else:
            intent_state = await get_active_intent_state(
                conversation_id,
                user_id,
                conversation_history,
                job_id_hint=_resolve_job_id_hint(
                    conversation_history=conversation_history,
                    intent_state=None,
                    latest_session=latest_extraction_session_for_classifier(conversation_history),
                ),
            )
            latest_session = latest_extraction_session_for_classifier(conversation_history)

            conv_session = None
            if conversation_id:
                from services.conversation.session_store import load_or_hydrate_session

                conv_session = await load_or_hydrate_session(str(user_id), str(conversation_id))
                if conv_session.intent_state and not intent_state:
                    intent_state = conv_session.intent_state
                if conv_session.active_job_id and not latest_session:
                    latest_session = conv_session.intent_state

            job_id_hint = _resolve_job_id_hint(
                conversation_history=conversation_history,
                intent_state=intent_state or latest_session,
                latest_session=latest_session,
            )
            if job_id_hint:
                from services.intent.job_context import load_job_context

                job_context = await load_job_context(
                    user_id=str(user_id),
                    job_id=job_id_hint,
                    conversation_id=str(conversation_id) if conversation_id else None,
                    conversation_history=conversation_history,
                )

            session_decision = await resolve_session_continuation(
                prompt,
                user_id=str(user_id),
                conversation_id=str(conversation_id) if conversation_id else None,
                conversation_history=conversation_history,
                intent_state=intent_state or latest_session,
                job_context=job_context,
                conv_session=conv_session,
            )

        from services.intent.route_turn import route_planning_path

        route = await route_planning_path(
            session_decision=session_decision,
            prompt=prompt,
            user_id=str(user_id),
            catalog=ctx.catalog,
            conversation_history=conversation_history,
            conversation_id=str(conversation_id) if conversation_id else None,
            job_context=job_context,
        )

        if route == "continuation_path" and job_context is None and not attached_file:
            job_id = _resolve_job_id_hint(
                conversation_history=conversation_history,
                intent_state=intent_state or latest_session,
                latest_session=latest_session,
            )
            if not job_id and conv_session and conv_session.active_job_id:
                job_id = str(conv_session.active_job_id)
            if not job_id:
                job_id = getattr(session_decision, "job_id", None)
                if job_id:
                    job_id = str(job_id)
            if job_id:
                from services.intent.job_context import load_job_context

                job_context = await load_job_context(
                    user_id=str(user_id),
                    job_id=job_id,
                    conversation_id=str(conversation_id) if conversation_id else None,
                    conversation_history=conversation_history,
                )

        if route == "continuation_path" and job_context is not None and not attached_file:
            from services.intent.continuation_planning import plan_from_job_context

            logger.info(
                "[classify_and_enrich] continuation_path job_id=%s",
                job_context.job_id,
            )
            return await plan_from_job_context(
                user_id=str(user_id),
                prompt=prompt,
                session_decision=session_decision,
                job=job_context,
                catalog=ctx.catalog,
                conversation_id=str(conversation_id) if conversation_id else None,
                conversation_history=conversation_history,
                plan_auto_run=plan_auto_run,
            )

        fresh_supersede = fresh_supersede_early or supersede_pending_for_fresh_turn(
            conversation_history
        )

        intent_state_for_pipeline = (
            (latest_session or intent_state)
            if session_action_is_continue(session_decision)
            else None
        )

        classification_result = _bootstrap_fresh_classification(
            prompt=prompt,
            session_decision=session_decision,
        )
        if attached_file:
            from services.file_extraction.pending_upload import (
                pin_file_upload_on_classification,
                promote_file_upload_session_from_classification,
            )

            classification_result = pin_file_upload_on_classification(
                classification_result,
                filename=explicit_source_name,
            )
            session_decision = promote_file_upload_session_from_classification(
                session_decision,
                classification_result,
            )
        classification_result = merge_session_decision_into_classification(
            classification_result,
            session_decision,
        )
        classification_result = merge_fresh_supersede_into_classification(
            classification_result,
            fresh_supersede,
        )

        if intent_state and str(intent_state.get("active_source") or "").lower() == "file":
            if str(classification_result.get("intent") or "").lower() == "cleanup":
                classification_result["source"] = "file"
                if not classification_result.get("source_name"):
                    sn = intent_state.get("source_name")
                    if sn:
                        classification_result["source_name"] = sn

        classification_result = await apply_post_classification_pipeline(
            classification_result,
            user_id=str(user_id),
            prompt=prompt,
            resource_catalog=ctx.catalog,
            intent_state_for_classifier=intent_state_for_pipeline,
            conversation_history=conversation_history,
            google_sheet_rows=ctx.google_sheet_rows,
            plan_auto_run=plan_auto_run,
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if PipelineLearningService.enabled() and prompt:
                learning_ctx = await PipelineLearningService.get_planning_retrieval_context(
                    user_id=str(user_id),
                    query_text=prompt,
                    source_type=classification_result.get("source"),
                    destination_type=classification_result.get("destination"),
                )
                if learning_ctx:
                    classification_result["learning_context"] = learning_ctx
        except Exception:
            pass
        return classification_result

    except Exception as e:
        print(f"❌ Error in classification: {e}")
        return _classification_error_fallback(reason=f"Error: {str(e)}")
