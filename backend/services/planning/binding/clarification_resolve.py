"""LLM resolution for planner clarification replies."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from services.planning.binding.observability import log_binding
from services.planning.binding.retrieve import candidates_to_prompt_json, retrieve_entity_shortlist

MIN_ENTITY_CLARIFY_CONFIDENCE = 0.55
MIN_REPLY_PARSE_CONFIDENCE = 0.55


async def resolve_entity_clarification_llm(
    reply_text: str,
    *,
    role: str,
    user_id: str,
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """
    Map free-text entity clarification (e.g. "use contact object") to catalog entity_key.
    Returns None when confidence is low or shortlist is empty.
    """
    text = (reply_text or "").strip()
    if not text:
        return None

    candidates, _, _ = await retrieve_entity_shortlist(
        str(user_id),
        classification,
        role=role,
        prompt=prompt or text,
        parsed_goal=parsed_goal,
        extra_hints=[text],
        k=12,
    )
    if not candidates:
        log_binding("clar_entity_llm", f"{role} skipped empty shortlist")
        return None

    catalog_json = candidates_to_prompt_json(candidates)
    llm_prompt = f"""You resolve which {role} table/object/sheet the user chose from a clarification reply.

User clarification reply:
\"\"\"{text}\"\"\"

Original migration request (context only):
\"\"\"{(prompt or '').strip()}\"\"\"

Candidates (entity_key MUST be from this list only):
{catalog_json}

Return ONLY valid JSON:
{{
  "entity_key": "<key from candidates or empty string>",
  "confidence": 0.0-1.0,
  "reasoning_short": "optional max 80 chars"
}}
"""

    t0 = time.perf_counter()
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_CLARIFICATION_ENTITY,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=200,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            log_binding("clar_entity_llm", "parse failed (no json)")
            return None
        data = json.loads(match.group(0))
        entity_key = str(data.get("entity_key") or "").strip()
        confidence = float(data.get("confidence") or 0.0)
        allowed = {c.entity_key for c in candidates}
        ms = (time.perf_counter() - t0) * 1000
        if entity_key not in allowed or confidence < MIN_ENTITY_CLARIFY_CONFIDENCE:
            log_binding(
                "clar_entity_llm",
                f"{role} unresolved key={entity_key or 'none'} conf={confidence:.2f} ms={ms:.0f}",
            )
            return None
        log_binding(
            "clar_entity_llm",
            f"{role} entity={entity_key} conf={confidence:.2f} ms={ms:.0f}",
        )
        return entity_key
    except Exception as exc:
        log_binding("clar_entity_llm", f"error: {exc}", level=40)
        return None


async def resolve_clarification_selections_llm(
    reply_text: str,
    clarifications: List[Dict[str, Any]],
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Unified LLM parser for free-text clarification replies.

    Returns planner selection payloads when confidence is sufficient; else [].
    """
    text = (reply_text or "").strip()
    if not text or not clarifications:
        return []

    blocking = [c for c in clarifications if c.get("required", True)]
    if len(blocking) != 1:
        return []
    target = blocking[0]
    cid = target.get("id") or ""
    ctype = (target.get("clarification_type") or "").strip()
    if not ctype:
        return []

    if ctype in ("pick_source_entity", "pick_destination_entity"):
        return await resolve_entity_clarification_selections(
            text,
            clarifications,
            user_id=str(user_id),
            classification=classification,
            prompt=prompt,
            conversation_id=conversation_id,
            parsed_goal=parsed_goal,
        )

    from services.intent.routing_context import build_routing_dialogue_context

    dialogue = build_routing_dialogue_context(
        conversation_history,
        current_prompt=text,
        max_entries=8,
    )
    options = target.get("options") or []
    options_block = json.dumps(options[:12], default=str) if options else "[]"

    llm_prompt = f"""Parse the user's clarification reply into a planner selection.

Open clarification type: {ctype}
Clarification prompt: {(target.get('prompt') or '')[:300]}

Original user goal:
\"\"\"{(prompt or '').strip()}\"\"\"

Recent conversation:
{dialogue}

User reply:
\"\"\"{text}\"\"\"

Options (if any — option_id must match when picking an option):
{options_block}

Return ONLY valid JSON:
{{
  "selections": [
    {{
      "clarification_id": "{cid}",
      "option_id": "<option id or fields_custom or text:VALUE or headers list token>",
      "text_value": "<optional raw text for field/header lists>"
    }}
  ],
  "confidence": 0.0-1.0
}}

Rules:
- pick_create_headers / pick_extract_fields: comma-separated fields → option_id "fields_custom", text_value = full reply.
- create_workbook_name: option_id "text:<title>", text_value = title.
- pick_workbook / connector pickers: use option id from options when user names a label.
- If cannot map, return empty selections and low confidence.
"""

    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_CLARIFICATION_REPLY,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=220,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return []
        data = json.loads(match.group(0))
        confidence = float(data.get("confidence") or 0.0)
        if confidence < MIN_REPLY_PARSE_CONFIDENCE:
            return []
        selections = data.get("selections") or []
        if not isinstance(selections, list):
            return []
        out: List[Dict[str, Any]] = []
        for sel in selections:
            if not isinstance(sel, dict):
                continue
            if str(sel.get("clarification_id") or "") != cid:
                continue
            payload = {
                "clarification_id": cid,
                "option_id": str(sel.get("option_id") or "").strip(),
            }
            if sel.get("text_value"):
                payload["text_value"] = str(sel.get("text_value")).strip()
            if payload.get("option_id"):
                out.append(payload)
        return out
    except Exception as exc:
        log_binding("clar_reply_llm", f"error: {exc}", level=40)
        return []


async def resolve_entity_clarification_selections(
    reply_text: str,
    clarifications: List[Dict[str, Any]],
    *,
    user_id: str,
    classification: Dict[str, Any],
    prompt: Optional[str] = None,
    conversation_id: Optional[str] = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build selection payloads from free-text entity clarification via small LLM."""
    text = (reply_text or "").strip()
    if not text or not clarifications:
        return []

    blocking = [c for c in clarifications if c.get("required", True)]
    if len(blocking) != 1:
        return []
    target = blocking[0]
    ctype = target.get("clarification_type") or ""
    if ctype not in ("pick_source_entity", "pick_destination_entity"):
        return []

    role = "destination" if ctype == "pick_destination_entity" else "source"
    entity_key = await resolve_entity_clarification_llm(
        text,
        role=role,
        user_id=str(user_id),
        classification=classification,
        prompt=prompt,
        conversation_id=conversation_id,
        parsed_goal=parsed_goal,
    )
    if not entity_key:
        return []

    prefix = "source" if role == "source" else "destination"
    return [
        {
            "clarification_id": target.get("id"),
            "option_id": f"{prefix}:{entity_key}",
        }
    ]
