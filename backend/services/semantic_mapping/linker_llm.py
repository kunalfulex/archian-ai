"""LLM refinement for ambiguous cross-connector entity pairs."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.models import GraphMember
from services.llm import tasks as llm_tasks
from services.llm.batch import map_with_concurrency
from services.llm.service import get_task_batch_settings
from services.semantic_mapping.config import (
    linker_ambiguous_high,
    linker_ambiguous_low,
    linker_llm_max_pairs,
)

logger = logging.getLogger(__name__)

ENTITY_LINK_SYSTEM = """You decide if two entities from different data systems represent the same business concept.
Output ONLY JSON: {"same_entity": true|false, "confidence": 0.0-1.0, "evidence": "brief reason", "suggested_canonical_name": "Name"}
Use category, descriptions, and column overlap. Platform/log tables are not the same as business entities."""

ENTITY_LINK_USER_TEMPLATE = """Entity A ({connector_a}):
{entity_a_json}

Entity B ({connector_b}):
{entity_b_json}

Same business entity?"""


def _entity_payload(ent: SourceEntity, member_name: str) -> Dict[str, Any]:
    return {
        "member": member_name,
        "entity_id": ent.entity_id,
        "label": ent.label,
        "category": ent.category,
        "category_description": ent.category_description,
        "object_name": ent.object_name,
        "columns": ent.column_names[:25],
    }


async def refine_ambiguous_candidates(
    candidates: List[Dict[str, Any]],
    inventory: List[SourceEntity],
    members: List[GraphMember],
) -> List[Dict[str, Any]]:
    """Boost or demote pairs in the ambiguous confidence band using LLM."""
    from services.architect_agent import async_openai_call

    inv_by_key = {(e.member_id, e.entity_id): e for e in inventory}
    member_names = {m.id: m.display_name for m in members}
    member_types = {m.id: m.connector_type for m in members}

    low, high = linker_ambiguous_low(), linker_ambiguous_high()
    ambiguous = [
        c
        for c in candidates
        if low <= float(c.get("confidence") or 0) < high
        and c.get("mapping_kind") != "flow_prior"
    ][: linker_llm_max_pairs()]

    if not ambiguous:
        return candidates

    batch_settings = await get_task_batch_settings(llm_tasks.EGA_ENTITY_LINK)
    max_concurrent = batch_settings.get("max_concurrent", 3)

    async def _refine_one(c: Dict[str, Any]) -> None:
        ea = inv_by_key.get((c["source_member_id"], c["source_entity_id"]))
        eb = inv_by_key.get((c["target_member_id"], c["target_entity_id"]))
        if not ea or not eb:
            return
        user = ENTITY_LINK_USER_TEMPLATE.format(
            connector_a=member_types.get(c["source_member_id"], ""),
            connector_b=member_types.get(c["target_member_id"], ""),
            entity_a_json=json.dumps(_entity_payload(ea, member_names.get(c["source_member_id"], "")), indent=2),
            entity_b_json=json.dumps(_entity_payload(eb, member_names.get(c["target_member_id"], "")), indent=2),
        )
        try:
            response = await async_openai_call(
                None,
                task=llm_tasks.EGA_ENTITY_LINK,
                messages=[
                    {"role": "system", "content": ENTITY_LINK_SYSTEM},
                    {"role": "user", "content": user},
                ],
                max_tokens=400,
                temperature=0.1,
            )
            from utils.openai_helpers import parse_openai_json_response

            parsed = parse_openai_json_response(response.choices[0].message.content.strip())
            if parsed.get("same_entity"):
                c["confidence"] = max(float(c.get("confidence") or 0), float(parsed.get("confidence") or 0.78))
                c["mapping_kind"] = "llm_refined"
            else:
                c["confidence"] = min(float(c.get("confidence") or 0), 0.45)
            ev = c.get("evidence") or {}
            if isinstance(ev, dict):
                ev["llm"] = parsed.get("evidence")
                ev["llm_same_entity"] = bool(parsed.get("same_entity"))
            c["evidence"] = ev
        except Exception as exc:
            logger.warning("Linker LLM pair failed: %s", exc)

    await map_with_concurrency(ambiguous, _refine_one, max_concurrent=max_concurrent)

    candidates.sort(key=lambda x: -float(x.get("confidence") or 0))
    return candidates
