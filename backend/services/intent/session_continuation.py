"""S01 — LLM session boundary: continue active job vs fresh pipeline."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger("edison.session_continuation")

SessionAction = Literal[
    "continue_active_job",
    "fresh_extract",
    "fresh_full_pipeline",
    "no_prior_job",
]
ContinuationPhase = Literal["map", "migrate", "data_ops", "none"]

MIN_SESSION_CONFIDENCE = 0.55


class SessionContinuationDecision(BaseModel):
    session_action: SessionAction = "no_prior_job"
    continuation_phase: ContinuationPhase = "none"
    reuse_job_id: bool = False
    job_id: Optional[str] = None
    confidence: float = 0.0
    reasoning_short: Optional[str] = None
    decision_source: Literal[
        "llm",
        "rules_fallback",
        "no_job",
        "progress_guardrail",
        "file_upload_guardrail",
    ] = "no_job"
    last_completed_step: Optional[str] = None
    session_progress: Optional[Dict[str, Any]] = None


def session_action_is_fresh(session_action: Optional[str]) -> bool:
    return (session_action or "") in ("fresh_extract", "fresh_full_pipeline")


def decision_from_classification(
    classification: Optional[Dict[str, Any]],
) -> Optional[SessionContinuationDecision]:
    cls = dict(classification or {})
    raw = cls.get("_session_continuation")
    if isinstance(raw, dict) and raw.get("session_action"):
        try:
            return SessionContinuationDecision.model_validate(raw)
        except Exception:
            pass
    sa = cls.get("session_action")
    if sa:
        try:
            return SessionContinuationDecision(
                session_action=sa,
                continuation_phase=cls.get("continuation_phase") or "none",
                reuse_job_id=bool(cls.get("active_job_id")) and sa == "continue_active_job",
                job_id=cls.get("active_job_id"),
                confidence=float(cls.get("session_confidence") or 0.0),
                decision_source="llm",
            )
        except Exception:
            return None
    return None


def _active_job_summary(
    conversation_history: Optional[List[Dict[str, Any]]],
    intent_state: Optional[Dict[str, Any]],
    conv_session: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    from services.planning.conversation_session import (
        latest_extraction_completed_metadata,
        latest_extraction_session_for_classifier,
    )

    meta = latest_extraction_completed_metadata(conversation_history) or {}
    session = latest_extraction_session_for_classifier(conversation_history) or {}
    job_id = (
        meta.get("job_id")
        or session.get("job_id")
        or session.get("active_job_id")
        or (intent_state or {}).get("job_id")
        or (intent_state or {}).get("active_job_id")
    )
    if not job_id and conv_session is not None:
        job_id = getattr(conv_session, "active_job_id", None)
        if not session and getattr(conv_session, "intent_state", None):
            session = conv_session.intent_state or {}
    if not job_id:
        return None

    return {
        "job_id": str(job_id),
        "state": (intent_state or {}).get("state") or session.get("state") or "extraction_completed",
        "source_type": meta.get("source_type") or meta.get("source") or session.get("active_source"),
        "source_name": meta.get("source_name") or session.get("source_name"),
        "destination": meta.get("destination") or session.get("active_destination"),
        "destination_name": meta.get("destination_name") or session.get("destination_name"),
        "record_count": meta.get("record_count"),
        "table_name": meta.get("table_name") or meta.get("object_api_name"),
        "extraction_version": meta.get("extraction_version"),
    }


def _continuation_phase_from_progress(progress: Dict[str, Any]) -> ContinuationPhase:
    """Mechanical continuation phase from job progress (no prompt keywords)."""
    next_step = (progress.get("next_logical_step") or "none").lower()
    if next_step == "migrate":
        return "migrate"
    if next_step == "map":
        return "map"
    if next_step == "data_ops":
        return "data_ops"
    last = (progress.get("last_completed_step") or "none").lower()
    if last == "map" and progress.get("mapping_ready"):
        return "migrate"
    return "none"


def _rules_fallback_decision(
    prompt: str,
    job_summary: Optional[Dict[str, Any]],
    progress_summary: Optional[Dict[str, Any]] = None,
) -> SessionContinuationDecision:
    """When S01 LLM fails: continue on active job using pipeline progress only."""
    if not job_summary or not job_summary.get("job_id"):
        return SessionContinuationDecision(
            session_action="no_prior_job",
            continuation_phase="none",
            reuse_job_id=False,
            decision_source="no_job",
            confidence=1.0,
            reasoning_short="No completed extraction in session",
        )

    job_id = str(job_summary["job_id"])
    progress = dict(progress_summary or {})

    return SessionContinuationDecision(
        session_action="continue_active_job",
        continuation_phase="none",
        reuse_job_id=True,
        job_id=job_id,
        confidence=0.55,
        decision_source="rules_fallback",
        reasoning_short="LLM unavailable — continue job; classifier/PLAN_SCOPE set phase",
        last_completed_step=progress.get("last_completed_step"),
    )


def _apply_progress_guardrails(
    decision: SessionContinuationDecision,
    progress: Dict[str, Any],
    *,
    prompt: Optional[str] = None,
) -> SessionContinuationDecision:
    """Trust S01 LLM; attach progress metadata only (no phase/session overrides)."""
    del prompt
    if not progress.get("job_id"):
        return decision
    out = decision.model_copy(deep=True)
    last = progress.get("last_completed_step")
    if last and not out.last_completed_step:
        out.last_completed_step = str(last)
    return out


def _apply_guardrails(
    decision: SessionContinuationDecision,
    job_summary: Optional[Dict[str, Any]],
    *,
    classification: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict]] = None,
    prompt: Optional[str] = None,
    progress_summary: Optional[Dict[str, Any]] = None,
) -> SessionContinuationDecision:
    job_id = (job_summary or {}).get("job_id")
    out = decision.model_copy(deep=True)

    if not job_id:
        out.session_action = "no_prior_job"
        out.reuse_job_id = False
        out.job_id = None
        out.continuation_phase = "none"
        return out

    out.job_id = str(job_id)

    if out.reuse_job_id and out.confidence < MIN_SESSION_CONFIDENCE:
        out.reuse_job_id = False
        out.session_action = "fresh_extract"
        out.continuation_phase = "none"
        out.reasoning_short = (out.reasoning_short or "") + " [low confidence → fresh]"
        return out

    if out.reuse_job_id and not out.job_id:
        out.reuse_job_id = False
        out.session_action = "fresh_extract"
        out.continuation_phase = "none"
        return out

    if out.session_action == "continue_active_job":
        out.reuse_job_id = True
    elif session_action_is_fresh(out.session_action):
        out.reuse_job_id = False

    if progress_summary:
        out = _apply_progress_guardrails(out, progress_summary, prompt=prompt)

    if progress_summary:
        out.session_progress = dict(progress_summary)
        if not out.last_completed_step:
            out.last_completed_step = progress_summary.get("last_completed_step")

    return out


async def resolve_session_continuation(
    prompt: str,
    *,
    user_id: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[str] = None,
    intent_state: Optional[Dict[str, Any]] = None,
    classification: Optional[Dict[str, Any]] = None,
    job_context: Optional[Any] = None,
    conv_session: Optional[Any] = None,
) -> SessionContinuationDecision:
    """
    S01 LLM: decide whether to reuse the active extraction job or start fresh.

    Skips LLM when no completed job exists in session metadata.
    When job_context is provided, uses job store facts instead of chat event scan.
    """
    existing = decision_from_classification(classification)
    if existing:
        return existing

    job_summary = _active_job_summary(conversation_history, intent_state, conv_session)
    if job_context is not None:
        from services.intent.job_context import JobContext

        if isinstance(job_context, JobContext):
            job_summary = job_context.s01_job_summary()
    if not job_summary:
        return SessionContinuationDecision(
            session_action="no_prior_job",
            continuation_phase="none",
            reuse_job_id=False,
            decision_source="no_job",
            confidence=1.0,
            reasoning_short="No prior extraction_completed event",
        )

    from services.planning.conversation_session import build_session_progress_summary

    if job_context is not None:
        from services.intent.job_context import JobContext

        if isinstance(job_context, JobContext):
            progress_summary = job_context.s01_progress_dict()
        else:
            progress_summary = build_session_progress_summary(
                conversation_history,
                job_id=str(job_summary["job_id"]),
            )
    else:
        progress_summary = build_session_progress_summary(
            conversation_history,
            job_id=str(job_summary["job_id"]),
        )

    from services.intent.routing_context import build_routing_dialogue_context

    dialogue = build_routing_dialogue_context(
        conversation_history, current_prompt=prompt or "", max_entries=10
    )
    llm_prompt = f"""You decide whether the user's message continues work on an EXISTING extraction job or starts a NEW data pipeline.

**Active extraction job (from session — do not invent a different job_id):**
{json.dumps(job_summary, indent=2)}

**Pipeline progress for this job (authoritative — includes pipeline_stage through migrated):**
{json.dumps(progress_summary, indent=2)}

**Recent conversation (oldest first):**
{dialogue}

**Current user message:**
\"\"\"{(prompt or "").strip()}\"\"\"

**Intent rules (infer meaning — typos and informal phrasing are OK):**
- fresh_extract: user wants NEW data pulled FROM a source (extract, pull, fetch, get records from …).
  NOT when they refer to data already extracted ("above/the/this extracted data", "the dataset") and
  want to move/export/copy/send/load it TO a destination — that is continue_active_job.
- fresh_full_pipeline: ONLY when user asks for a NEW compound extract AND load/migrate/deploy data
  in the SAME request. NOT for "start migration" after mapping is done.
- continue_active_job: user works on data ALREADY extracted in the active job — map/remap, migrate/load
  (move, export to, copy to, send to, push to, transfer to, sync to), filter, dedupe, validate,
  transform, clean invalid rows.
- pipeline_stage=migrated means the full cycle completed on this job — the job is NOT closed. User may re-migrate, run data ops (continue), OR request a fresh extract (fresh_extract).

**Progress rules:**
- Use last_completed_step, next_logical_step, and steps.*.status as ground truth.
- "start the migration" when last_completed_step=map → continue_active_job, continuation_phase=migrate (NOT fresh_full_pipeline).
- After extract, map is NOT automatic — set continuation_phase from what the USER asked: data cleanup → data_ops; map/remap/align fields → map; load/move/export/copy/send to a named target → migrate; ambiguous short command → use next_logical_step as a hint only.
- When pipeline_stage=migrated, next_logical_step is none — still allow continue_active_job for re-migrate/data ops.

Set reuse_job_id true ONLY for continue_active_job.
Set continuation_phase: map | migrate | data_ops | none (match the user's requested work, not a default "next step").

Return ONLY valid JSON:
{{
  "session_action": "continue_active_job|fresh_extract|fresh_full_pipeline|no_prior_job",
  "continuation_phase": "map|migrate|data_ops|none",
  "reuse_job_id": true|false,
  "confidence": 0.0-1.0,
  "reasoning_short": "one sentence"
}}
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.SESSION_CONTINUATION,
            messages=[{"role": "user", "content": llm_prompt}],
            response_format={"type": "json_object"},
            max_tokens=220,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            raise ValueError("non-object JSON")

        sa = str(data.get("session_action") or "").strip()
        if sa not in (
            "continue_active_job",
            "fresh_extract",
            "fresh_full_pipeline",
            "no_prior_job",
        ):
            raise ValueError(f"invalid session_action: {sa}")

        phase = str(data.get("continuation_phase") or "none").strip().lower()
        if phase not in ("map", "migrate", "data_ops", "none"):
            phase = "none"

        decision = SessionContinuationDecision(
            session_action=sa,  # type: ignore[arg-type]
            continuation_phase=phase,  # type: ignore[arg-type]
            reuse_job_id=bool(data.get("reuse_job_id")),
            job_id=str(job_summary["job_id"]),
            confidence=float(data.get("confidence") or 0.0),
            reasoning_short=str(data.get("reasoning_short") or "")[:200] or None,
            decision_source="llm",
        )
        logger.info(
            "[session_continuation] llm action=%s phase=%s reuse=%s conf=%.2f job=%s last_step=%s",
            decision.session_action,
            decision.continuation_phase,
            decision.reuse_job_id,
            decision.confidence,
            decision.job_id,
            progress_summary.get("last_completed_step"),
        )
    except Exception as exc:
        logger.warning("[session_continuation] LLM failed, rules fallback: %s", exc)
        decision = _rules_fallback_decision(prompt, job_summary, progress_summary)

    result = _apply_guardrails(
        decision,
        job_summary,
        classification=classification,
        conversation_history=conversation_history,
        prompt=prompt,
        progress_summary=progress_summary,
    )
    logger.info(
        "[session_continuation] final action=%s phase=%s route_hint=%s conf=%.2f source=%s stage=%s",
        result.session_action,
        result.continuation_phase,
        "fresh" if session_action_is_fresh(result.session_action) else "continue",
        result.confidence,
        result.decision_source,
        progress_summary.get("pipeline_stage"),
    )
    return result


def apply_session_decision_to_state(
    state: Any,
    decision: SessionContinuationDecision,
) -> None:
    state.session_action = decision.session_action
    state.continuation_phase = decision.continuation_phase
    state.session_confidence = decision.confidence
    state.fresh_extract = session_action_is_fresh(decision.session_action)
    state.active_job_id = decision.job_id if decision.reuse_job_id else None
    state.continuation_turn = (
        decision.session_action == "continue_active_job" and bool(decision.reuse_job_id)
    )
    if decision.last_completed_step:
        state.last_completed_step = decision.last_completed_step


def merge_session_decision_into_classification(
    classification: Dict[str, Any],
    decision: SessionContinuationDecision,
) -> Dict[str, Any]:
    out = dict(classification or {})
    out["session_action"] = decision.session_action
    out["continuation_phase"] = decision.continuation_phase
    out["session_confidence"] = decision.confidence
    out["_session_continuation"] = decision.model_dump(mode="json")
    if decision.last_completed_step:
        out["last_completed_step"] = decision.last_completed_step
    if decision.session_progress:
        out["_session_progress"] = dict(decision.session_progress)

    if decision.reuse_job_id and decision.job_id:
        out["active_job_id"] = decision.job_id
    elif session_action_is_fresh(decision.session_action):
        out.pop("active_job_id", None)

    return out
