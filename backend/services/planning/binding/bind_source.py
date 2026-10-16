"""LLM bind: source extract entity + fields."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from services.planning.binding.contract import EntityCandidate, SourceBindLLMOutput
from services.planning.binding.observability import log_binding
from services.planning.binding.retrieve import candidates_to_prompt_json


async def bind_source_extract_llm(
    prompt: str,
    *,
    user_id: str,
    classification: Dict[str, Any],
    candidates: List[EntityCandidate],
    parsed_goal: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> SourceBindLLMOutput:
    """Pick source entity_key and field_keys from shortlist only."""
    if not candidates:
        log_binding("bind_source", "skipped empty shortlist")
        return SourceBindLLMOutput(confidence=0.0)

    goal = parsed_goal or {}
    source_phrase = goal.get("source_phrase") or ""
    catalog_json = candidates_to_prompt_json(candidates)

    llm_prompt = f"""You select the SOURCE table/object/sheet and extract fields for a data pipeline.

User request:
\"\"\"{(prompt or '').strip()}\"\"\"

Source connector: {classification.get('source_name') or classification.get('source') or 'unknown'}
Source phrase from goal parser: {source_phrase or '(none)'}

Candidates (pick entity_key ONLY from this list; field_keys ONLY from that entity's fields):
Each candidate includes relationship_targets: lookup parent objects whose descriptive fields
may appear in the SAME extract query (e.g. Contact + Account name on Contact rows).

{catalog_json}

Return ONLY valid JSON:
{{
  "entity_key": "<primary extract object from candidates>",
  "extract_field_scope": "explicit|vague|all_descriptive|unspecified",
  "field_keys": ["columns on PRIMARY object only when user named columns"],
  "related_entity_key": null,
  "related_field_keys": [],
  "confidence": 0.0-1.0,
  "reasoning_short": "optional max 120 chars"
}}

Rules:
- field_keys and related_field_keys MUST use each field's "key" (API/column name), never display label.
- Pick PRIMARY using label, category, description, synonyms (business language OK).
- Set related_entity_key ONLY when the user asks for another object's descriptive fields
  in the SAME extract (e.g. "customer contact with business details" → Contact + Account).
- related_entity_key MUST be one of relationship_targets on the chosen primary — never invent.
- Use related target label/category/description/synonyms to interpret business language
  (e.g. "business details" → Account when listed under Contact's relationship_targets).
- field_keys = PRIMARY object fields only. related_field_keys = tokens for parent fields
  (name, phone, website) — not necessarily API names.
- Do NOT set related_entity_key for map/migrate destination language.
- "along with their / associated" → related lookup parent, not a second base extract.
- If the user named specific primary columns, set extract_field_scope=explicit, confidence >= 0.85.
- "user table" with person/contact columns means a business USER table (e.g. users), NOT metadata tables.
- Do not invent entity_key or field_keys outside the candidate lists.

Examples:
- contact first name, email only → {{"entity_key": "Contact", "field_keys": ["FirstName", "Email"], "related_entity_key": null, "related_field_keys": []}}
- customer contact + business details → {{"entity_key": "Contact", "field_keys": [...], "related_entity_key": "Account", "related_field_keys": ["name", "phone"]}}
- contact + account name and account phone → {{"entity_key": "Contact", "related_entity_key": "Account", "related_field_keys": ["name", "phone"]}}
"""

    t0 = time.perf_counter()
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_BIND_SOURCE_EXTRACT,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=520,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            log_binding("bind_source", "llm parse failed (no json)")
            return SourceBindLLMOutput(confidence=0.0)
        data = json.loads(match.group(0))
        out = SourceBindLLMOutput(
            entity_key=str(data.get("entity_key") or "").strip(),
            extract_field_scope=str(data.get("extract_field_scope") or "unspecified").lower(),  # type: ignore[arg-type]
            field_keys=[str(x).strip() for x in (data.get("field_keys") or []) if str(x).strip()],
            related_entity_key=(data.get("related_entity_key") or None),
            related_field_keys=[
                str(x).strip() for x in (data.get("related_field_keys") or []) if str(x).strip()
            ],
            confidence=float(data.get("confidence") or 0.0),
            reasoning_short=(data.get("reasoning_short") or "")[:120] or None,
        )
        ms = (time.perf_counter() - t0) * 1000
        log_binding(
            "bind_source",
            f"entity={out.entity_key or 'none'} related={out.related_entity_key or '-'} "
            f"scope={out.extract_field_scope} "
            f"fields={len(out.field_keys)} related_fields={len(out.related_field_keys)} "
            f"conf={out.confidence:.2f} ms={ms:.0f}",
        )
        return out
    except Exception as exc:
        log_binding("bind_source", f"llm error: {exc}", level=40)
        return SourceBindLLMOutput(confidence=0.0)
