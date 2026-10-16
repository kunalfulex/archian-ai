"""LLM bind: destination entity + fields (map/migrate)."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional

from services.planning.binding.contract import DestinationBindLLMOutput, EntityCandidate
from services.planning.binding.observability import log_binding
from services.planning.binding.retrieve import candidates_to_prompt_json
from services.planning.schemas import normalize_connector_type


def should_bind_destination(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    from services.planning.plan_scope import continuation_pipeline_tokens

    tokens = continuation_pipeline_tokens(classification, parsed_goal)
    if "migrate" in tokens and classification.get("active_job_id"):
        from services.planning.entity_pins import destination_entity_confirmed, pinned_entity_key

        if destination_entity_confirmed(classification) or pinned_entity_key(
            classification, role="destination"
        ):
            return False
        progress = classification.get("_session_progress") or {}
        if progress.get("mapping_ready"):
            return False

    ops = [
        str(o).lower()
        for o in (parsed_goal or {}).get("operations") or classification.get("operations") or []
    ]
    if not any(o in ops for o in ("migrate", "map", "deploy_schema")):
        return False
    dest = normalize_connector_type(classification.get("destination") or "")
    return dest not in ("unknown", "", "file")


async def bind_destination_target_llm(
    prompt: str,
    *,
    user_id: str,
    classification: Dict[str, Any],
    candidates: List[EntityCandidate],
    parsed_goal: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    conversation_history: Optional[List] = None,
    source_entity_key: Optional[str] = None,
    source_entity_label: Optional[str] = None,
) -> DestinationBindLLMOutput:
    if not candidates:
        log_binding("bind_dest", "skipped empty shortlist")
        return DestinationBindLLMOutput(field_scope="none", confidence=0.0)

    goal = parsed_goal or {}
    dest_phrase = goal.get("destination_phrase") or ""
    catalog_json = candidates_to_prompt_json(candidates)

    context_block = ""
    if conversation_history:
        from services.intent.routing_context import build_routing_dialogue_context
        from services.planning.session_facts import build_conversation_session_facts

        dialogue = build_routing_dialogue_context(
            conversation_history,
            current_prompt=prompt or "",
            max_entries=8,
        )
        facts = build_conversation_session_facts(conversation_history)
        if dialogue and dialogue != "None":
            context_block += f"\nRecent conversation:\n{dialogue}\n"
        if facts:
            context_block += f"\nSession facts:\n{json.dumps(facts, indent=2, default=str)}\n"

    job_block = ""
    if source_entity_key or classification.get("active_job_id"):
        job_block = (
            f"\nActive extract job source entity: "
            f"{source_entity_label or source_entity_key or '(unknown)'}"
            f"{f' ({source_entity_key})' if source_entity_key and source_entity_label else ''}\n"
            "When the user did not name a different destination object, prefer the same "
            "business entity as the extract source (use candidate label/category/synonyms).\n"
        )

    llm_prompt = f"""You select the DESTINATION table/object/sheet and optional target fields.

User request:
\"\"\"{(prompt or '').strip()}\"\"\"
{context_block}{job_block}
Destination connector: {classification.get('destination_name') or classification.get('destination') or 'unknown'}
Destination phrase: {dest_phrase or '(none)'}

Candidates (entity_key and field_keys must come from this list only):
Each candidate includes match_score and match_reasons from catalog retrieval — prefer higher scores
when the user did not explicitly name a different object.

{catalog_json}

Return ONLY valid JSON:
{{
  "entity_key": "<from candidates or empty if none>",
  "field_scope": "explicit|vague|all_descriptive|none",
  "field_keys": [],
  "confidence": 0.0-1.0,
  "reasoning_short": "optional"
}}

Rules:
- If the user named a destination object/table/sheet, pick that entity even when match_score is lower.
- If the user only named the destination connector/org, default to the same entity as the active extract job when it appears in candidates.
- Use label, category, description, synonyms to interpret business language.
"""

    t0 = time.perf_counter()
    try:
        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.PLAN_BIND_DESTINATION,
            messages=[{"role": "user", "content": llm_prompt}],
            max_tokens=350,
            temperature=0.0,
            user_id=str(user_id),
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        raw = (response.choices[0].message.content or "").strip()
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            log_binding("bind_dest", "llm parse failed (no json)")
            return DestinationBindLLMOutput(field_scope="none", confidence=0.0)
        data = json.loads(match.group(0))
        out = DestinationBindLLMOutput(
            entity_key=str(data.get("entity_key") or "").strip(),
            field_scope=str(data.get("field_scope") or "none").lower(),  # type: ignore[arg-type]
            field_keys=[str(x).strip() for x in (data.get("field_keys") or []) if str(x).strip()],
            confidence=float(data.get("confidence") or 0.0),
            reasoning_short=(data.get("reasoning_short") or "")[:120] or None,
        )
        ms = (time.perf_counter() - t0) * 1000
        log_binding(
            "bind_dest",
            f"entity={out.entity_key or 'none'} scope={out.field_scope} "
            f"fields={len(out.field_keys)} conf={out.confidence:.2f} ms={ms:.0f}",
        )
        return out
    except Exception as exc:
        log_binding("bind_dest", f"llm error: {exc}", level=40)
        return DestinationBindLLMOutput(field_scope="none", confidence=0.0)
