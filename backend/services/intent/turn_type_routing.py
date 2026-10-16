"""Gate 0 — LLM turn-family routing (info / general / pipeline) before S01 or classifier."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TurnFamily = Literal["info", "general", "pipeline"]
PlanRoute = Literal["info_path", "general_path"]

MIN_GATE0_CONFIDENCE = 0.55


class TurnTypeRoutingDecision(BaseModel):
    turn_family: TurnFamily = "pipeline"
    plan_route: Optional[PlanRoute] = None
    confidence: float = 0.0
    conversational_turn: bool = False
    off_topic: bool = False
    reasoning_short: Optional[str] = None
    decision_source: Literal["llm", "rules_fallback", "low_confidence_fallback"] = "llm"


def _family_to_plan_route(family: TurnFamily) -> Optional[PlanRoute]:
    if family == "info":
        return "info_path"
    if family == "general":
        return "general_path"
    return None


async def resolve_turn_type_routing(
    prompt: str,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> TurnTypeRoutingDecision:
    """
    Gate 0: decide info vs general vs pipeline before job load, S01, or classifier.

    Falls back to default pipeline when the LLM fails.
    """
    pl = (prompt or "").strip()
    if not pl:
        return TurnTypeRoutingDecision(
            turn_family="general",
            plan_route="general_path",
            confidence=1.0,
            conversational_turn=True,
            decision_source="rules_fallback",
            reasoning_short="Empty prompt",
        )

    from services.intent.routing_context import build_routing_dialogue_context
    from services.planning.plan_context import build_open_clarification_routing_block

    dialogue = build_routing_dialogue_context(
        conversation_history, current_prompt=pl, max_entries=10
    )
    open_clar_block = build_open_clarification_routing_block(conversation_history)
    llm_prompt = f"""You route the user's message into exactly one turn family for Archian AI (data pipeline assistant).

**Turn families:**
- **info** — Questions about schema, connectors, objects, fields, tables, or WHERE data lives. No action to create, deploy, extract, migrate, map, or transform data.
- **general** — Greetings, thanks, capabilities, off-topic chat. No pipeline work requested.
- **pipeline** — ANY requested action on data or connectors: create resources (sheets, tables, flows), deploy metadata/schema, extract/pull data, map fields, migrate/load, filter/dedupe/validate/transform/clean data, join sources, or continue work on an active job.

**Principles (use conversation context — do not rely on single-word triggers):**
- Schema/location questions without an action → **info**.
- Greetings, "what can you do?", unrelated coding homework → **general**.
- If the user wants something DONE (create, deploy, extract, map, migrate, clean, transform, continue prior work) → **pipeline**.
- Follow-up turns inherit thread intent (e.g. prior turn asked to create a sheet; "just create it" → **pipeline**).
- Short replies that answer a prior assistant clarification (column names, org name, entity) → **pipeline**, not info.
- When open planner clarification gaps exist below, short replies that fill those gaps → **pipeline**, not info or general.
- Pure schema/location questions that do NOT fill an open gap → **info** even when clarifications are open.
- SOQL/SQL to pull data → **pipeline** (in scope, not off_topic).
- Prior extraction or plans in history do NOT turn a pure info question into pipeline.
{open_clar_block}
**Recent conversation (oldest first):**
{dialogue}

**Current user message:**
\"\"\"{pl}\"\"\"

Return ONLY valid JSON:
{{
  "turn_family": "info|general|pipeline",
  "confidence": 0.0,
  "conversational_turn": false,
  "off_topic": false,
  "reasoning_short": "one sentence"
}}
"""
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks
        from utils.openai_helpers import parse_openai_json_response

        response = await async_openai_call(
            None,
            task=llm_tasks.TURN_TYPE_ROUTING,
            messages=[{"role": "user", "content": llm_prompt}],
            response_format={"type": "json_object"},
            max_tokens=180,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = response.choices[0].message.content or "{}"
        data = parse_openai_json_response(raw)
        if not isinstance(data, dict):
            raise ValueError("non-object JSON")

        family = str(data.get("turn_family") or "").strip().lower()
        if family not in ("info", "general", "pipeline"):
            raise ValueError(f"invalid turn_family: {family}")

        confidence = float(data.get("confidence") or 0.0)
        decision = TurnTypeRoutingDecision(
            turn_family=family,  # type: ignore[arg-type]
            plan_route=_family_to_plan_route(family),  # type: ignore[arg-type]
            confidence=confidence,
            conversational_turn=bool(data.get("conversational_turn")),
            off_topic=bool(data.get("off_topic")),
            reasoning_short=str(data.get("reasoning_short") or "").strip() or None,
            decision_source="llm",
        )

        logger.debug(
            "[gate0] turn_family=%s conf=%.2f source=llm",
            decision.turn_family,
            decision.confidence,
        )
        return decision
    except Exception as exc:
        logger.warning("[gate0] LLM failed, default pipeline: %s", exc)
        return TurnTypeRoutingDecision(
            turn_family="pipeline",
            plan_route=None,
            confidence=0.0,
            decision_source="rules_fallback",
            reasoning_short="LLM unavailable; default pipeline",
        )
