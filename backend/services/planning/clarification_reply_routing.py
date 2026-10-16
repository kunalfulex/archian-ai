"""LLM judgment: is the user's message answering an open planner clarification?"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MIN_REPLY_CONFIDENCE = 0.55


class ClarificationReplyDecision(BaseModel):
    is_clarification_reply: bool = False
    confidence: float = 0.0
    reasoning_short: Optional[str] = None
    decision_source: str = "llm"


def _open_clarification_summary(clarifications: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for clar in clarifications or []:
        if not isinstance(clar, dict):
            continue
        ctype = (clar.get("clarification_type") or "").strip()
        prompt = (clar.get("prompt") or "").strip()
        if ctype:
            lines.append(f"- {ctype}: {prompt[:200] if prompt else '(no prompt)'}")
    return "\n".join(lines) if lines else "(unknown gap)"


async def resolve_clarification_reply_routing(
    prompt: str,
    *,
    user_id: str,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    conversation_id: Optional[str] = None,
    original_prompt: Optional[str] = None,
    open_clarifications: Optional[List[Dict[str, Any]]] = None,
) -> ClarificationReplyDecision:
    """
    Decide whether the current message continues an open clarification thread.

    Uses dialogue context + open gap metadata. Falls back to legacy heuristics only
    when the LLM is unavailable.
    """
    text = (prompt or "").strip()
    if not text or not open_clarifications:
        return ClarificationReplyDecision(
            is_clarification_reply=False,
            confidence=1.0,
            decision_source="no_pending",
        )

    from services.intent.routing_context import build_routing_dialogue_context

    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=text,
        max_entries=10,
    )
    gap_block = _open_clarification_summary(open_clarifications or [])
    original = (original_prompt or "").strip()

    llm_prompt = f"""You decide if the user's latest message is answering an OPEN planner clarification (not a new unrelated request).

**Original user goal (when clarification started):**
\"\"\"{original or '(unknown)'}\"\"\"

**Open clarification gap(s):**
{gap_block}

**Recent conversation (oldest first):**
{dialogue}

**Latest user message:**
\"\"\"{text}\"\"\"

Rules:
- Field lists, org names, entity names, workbook titles, "yes/that one", or short details that fill the open gap → is_clarification_reply true.
- New unrelated goals (extract from X, migrate to Y, greetings, general schema questions) → false.
- If assistant just asked for column headers on a create-sheet request and user lists names → true.
- If user starts a wholly new pipeline request → false.

Return ONLY valid JSON:
{{
  "is_clarification_reply": true|false,
  "confidence": 0.0,
  "reasoning_short": "..."
}}
"""

    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_CLARIFICATION_REPLY,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=120,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            is_reply = bool(data.get("is_clarification_reply"))
            conf = float(data.get("confidence") or 0.0)
            return ClarificationReplyDecision(
                is_clarification_reply=is_reply and conf >= MIN_REPLY_CONFIDENCE,
                confidence=conf,
                reasoning_short=str(data.get("reasoning_short") or "")[:200] or None,
                decision_source="llm",
            )
    except Exception as exc:
        logger.warning("[clarification_reply_routing] LLM failed: %s", exc)

    heuristic = False
    return ClarificationReplyDecision(
        is_clarification_reply=heuristic,
        confidence=0.0,
        reasoning_short="LLM unavailable — conservative false",
        decision_source="llm_unavailable",
    )
