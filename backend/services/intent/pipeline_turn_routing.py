"""Unified pipeline turn routing — planning continuation vs job vs fresh (after Gate 0)."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

PipelineBranch = Literal["planning_continuation", "job_continuation", "fresh"]


class PipelineTurnRoutingError(RuntimeError):
    """Pipeline turn LLM call or response parse failed."""


class PipelineTurnDecision(BaseModel):
    branch: PipelineBranch = "fresh"
    confidence: float = 0.0
    reasoning_short: Optional[str] = None
    decision_source: str = "llm"


@dataclass(frozen=True)
class PipelineTurnContext:
    user_message: str
    pending: Optional[Dict[str, Any]]
    open_clarifications: List[Dict[str, Any]]
    extract_pending: Optional[Dict[str, Any]]
    job_summary: Optional[Dict[str, Any]]
    dialogue: str
    original_prompt: str
    extract_original_prompt: str
    stalled_planner_pending: bool
    extract_step_pending: bool
    ranked_slot_types: List[str]
    plan_dismissed_recently: bool


def _job_summary_block(job_summary: Optional[Dict[str, Any]]) -> str:
    if not job_summary or not job_summary.get("job_id"):
        return "(no active job)"
    parts = [f"job_id={job_summary.get('job_id')}"]
    for key in ("state", "source_type", "source_name", "destination", "destination_name", "table_name"):
        val = job_summary.get(key)
        if val:
            parts.append(f"{key}={val}")
    return ", ".join(parts)


def _recent_plan_dismissed(conversation_history: Optional[List[Dict[str, Any]]]) -> bool:
    for msg in reversed(conversation_history or []):
        meta = msg.get("metadata")
        if not isinstance(meta, dict):
            continue
        if meta.get("plan_dismissed") or meta.get("event_type") == "plan_dismissed":
            return True
        if meta.get("event_type") == "planner_clarification_required":
            return False
    return False


def gather_pipeline_turn_context(
    prompt: str,
    conversation_history: Optional[List[Dict[str, Any]]],
) -> PipelineTurnContext:
    from services.intent.routing_context import build_routing_dialogue_context
    from services.intent.session_continuation import _active_job_summary
    from services.planning.clarification_queue import open_ranked_slot_clarification_types
    from services.planning.plan_context import (
        find_extract_step_pending,
        open_clarifications_for_routing,
    )

    text = (prompt or "").strip()
    pending, open_clarifications = open_clarifications_for_routing(conversation_history)
    extract_pending = find_extract_step_pending(conversation_history)
    job_summary = _active_job_summary(conversation_history, None)
    has_open_clar = bool(open_clarifications)
    stalled_planner_pending = bool(
        pending
        and not has_open_clar
        and not (pending or {}).get("plan_execution_pending")
    )
    extract_step_pending = bool(extract_pending)
    original = ((pending or {}).get("original_prompt") or "").strip()
    extract_original = ((extract_pending or {}).get("original_prompt") or "").strip()
    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=text,
        max_entries=10,
    )
    ranked = sorted(open_ranked_slot_clarification_types(open_clarifications))

    return PipelineTurnContext(
        user_message=text,
        pending=pending,
        open_clarifications=list(open_clarifications or []),
        extract_pending=extract_pending,
        job_summary=job_summary,
        dialogue=dialogue,
        original_prompt=original,
        extract_original_prompt=extract_original,
        stalled_planner_pending=stalled_planner_pending,
        extract_step_pending=extract_step_pending,
        ranked_slot_types=ranked,
        plan_dismissed_recently=_recent_plan_dismissed(conversation_history),
    )


def build_pipeline_turn_routing_prompt(ctx: PipelineTurnContext) -> str:
    from services.planning.plan_context import format_open_clarification_summary

    open_gap_block = format_open_clarification_summary(ctx.open_clarifications)
    ranked_block = ", ".join(ctx.ranked_slot_types) if ctx.ranked_slot_types else "(none)"
    planner_state = json.dumps(
        {
            "stalled_planner_pending": ctx.stalled_planner_pending,
            "extract_step_pending": ctx.extract_step_pending,
            "ranked_slot_clarifications": ctx.ranked_slot_types,
            "plan_dismissed_recently": ctx.plan_dismissed_recently,
        },
        indent=2,
    )

    return f"""You route a pipeline turn after Gate 0 already classified it as actionable pipeline work.

Choose exactly one branch:

- **planning_continuation** — The latest user message answers an OPEN planner clarification gap, supplies a missing slot value, picks an option, or narrows/corrects scope on the same in-flight plan (e.g. "just extract", "extract only", "no migrate"). Short replies that clearly fill the open gap count.
- **job_continuation** — The latest user message continues work on extracted data in the ACTIVE JOB (map fields, migrate/load, filter, transform) without starting a wholly new extract or replan from scratch.
- **fresh** — The latest user message starts a NEW pipeline goal (new extract, new migrate, new destination, new create-sheet request) that should replace any stale clarification panel. The user's current intent overrides background context.

**Primary signal — latest user message (decide from this first):**
\"\"\"{ctx.user_message}\"\"\"

**Planner state (background context only):**
{planner_state}

**Open planner clarification gap(s):**
{open_gap_block}

**Original goal when clarification started:**
\"\"\"{ctx.original_prompt or ctx.extract_original_prompt or '(unknown)'}\"\"\"

**Extract-step thread original (if any):**
\"\"\"{ctx.extract_original_prompt or '(none)'}\"\"\"

**Ranked-slot clarifications (must pick before plan can proceed — informational):**
{ranked_block}

**Active job (if any):**
{_job_summary_block(ctx.job_summary)}

**Recent conversation (oldest first):**
{ctx.dialogue}

Rules:
- Latest user message is the primary intent. Open gaps, original_prompt, and job summary are background — do not let them override a clear new goal in the latest message.
- create_workbook_name / spreadsheet title gaps expect a SHORT title (e.g. "Q1 Leads"), not a full migration or extract sentence.
- Field lists, connector picks, "yes/that one", short workbook titles → planning_continuation when they fill the open gap.
- Scope corrections on the same plan ("just extract", "extract only", "no destination") → planning_continuation, NOT fresh.
- Full new pipeline sentences ("extract X from Y and migrate to Z", new source/destination unrelated to open gap) → fresh (supersedes stale panel).
- "Map to Contact", "migrate to Test DB" on existing extracted data when a job exists → job_continuation.
- If plan_dismissed_recently is true, treat the latest message as steering a new plan — prefer fresh unless the message clearly answers a still-relevant open gap.

Return ONLY valid JSON:
{{
  "branch": "planning_continuation"|"job_continuation"|"fresh",
  "confidence": 0.0,
  "reasoning_short": "..."
}}
"""


def parse_pipeline_turn_response(raw: str) -> PipelineTurnDecision:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise PipelineTurnRoutingError(f"No JSON object in LLM response: {raw[:200]!r}")

    data = json.loads(match.group(0))
    branch_raw = str(data.get("branch") or "fresh").strip().lower()
    branch: PipelineBranch = "fresh"
    if branch_raw in ("planning_continuation", "planning", "clarification"):
        branch = "planning_continuation"
    elif branch_raw in ("job_continuation", "job", "continue_job"):
        branch = "job_continuation"

    conf = float(data.get("confidence") or 0.0)
    logger.info(
        "[pipeline_turn_routing] branch=%s conf=%.2f source=llm",
        branch,
        conf,
    )
    return PipelineTurnDecision(
        branch=branch,
        confidence=conf,
        reasoning_short=str(data.get("reasoning_short") or "")[:200] or None,
        decision_source="llm",
    )


async def invoke_pipeline_turn_routing_llm(
    llm_prompt: str,
    *,
    user_id: str,
    conversation_id: Optional[str],
) -> PipelineTurnDecision:
    from services.architect_agent import async_openai_call
    from services.llm import tasks as llm_tasks

    response = await async_openai_call(
        None,
        task=llm_tasks.PIPELINE_TURN_ROUTING,
        messages=[{"role": "user", "content": llm_prompt}],
        max_tokens=150,
        temperature=0.0,
        user_id=str(user_id),
        conversation_id=str(conversation_id) if conversation_id else None,
    )
    raw = (response.choices[0].message.content or "").strip()
    return parse_pipeline_turn_response(raw)


async def resolve_pipeline_turn(
    prompt: str,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> PipelineTurnDecision:
    """
    After Gate 0 chose pipeline, decide planning continuation vs job vs fresh.

    Gathers session context, calls the routing LLM, and returns its branch decision.
    """
    ctx = gather_pipeline_turn_context(prompt, conversation_history)
    llm_prompt = build_pipeline_turn_routing_prompt(ctx)
    try:
        return await invoke_pipeline_turn_routing_llm(
            llm_prompt,
            user_id=user_id,
            conversation_id=conversation_id,
        )
    except Exception as exc:
        logger.warning("[pipeline_turn_routing] LLM failed: %s", exc)
        raise PipelineTurnRoutingError(str(exc)) from exc
