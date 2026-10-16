"""LLM-first graph-level cross-connector entity mapping."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.models import GraphMember
from services.llm import tasks as llm_tasks
from services.semantic_mapping.ega_intelligence_bundle import (
    build_ega_intelligence_bundle,
    bundle_to_prompt_json,
)
from services.semantic_mapping.linking_validator import validate_entity_mapping_clusters
from services.semantic_mapping.prompts.entity_linking_v1 import (
    ENTITY_LINKING_SYSTEM,
    ENTITY_LINKING_USER_TEMPLATE,
)
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)


async def propose_entity_mappings_llm(
    *,
    graph_name: str,
    graph_settings: Optional[Dict[str, Any]],
    members: List[GraphMember],
    inventory: List[SourceEntity],
    user_id: str,
    semantic_mappings: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    Returns (mapping clusters, rationale).
    Raises on LLM/parse failures or when no valid clusters remain.
    """
    from services.architect_agent import async_openai_call

    bundle = await build_ega_intelligence_bundle(
        graph_name=graph_name,
        graph_settings=graph_settings,
        members=members,
        inventory=inventory,
        user_id=user_id,
        semantic_mappings=semantic_mappings,
    )
    if not bundle.get("entities_by_member"):
        raise ValueError("No assessable entities in intelligence bundle")

    user_prompt = ENTITY_LINKING_USER_TEMPLATE.format(
        graph_name=graph_name,
        bundle_json=bundle_to_prompt_json(bundle),
    )
    response = await async_openai_call(
        None,
        task=llm_tasks.EGA_ENTITY_MAPPING,
        messages=[
            {"role": "system", "content": ENTITY_LINKING_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=6000,
        temperature=0.15,
    )
    parsed = parse_openai_json_response(response.choices[0].message.content.strip())
    clusters, warnings = validate_entity_mapping_clusters(parsed, inventory)
    if warnings:
        logger.info("Entity mapping LLM validation warnings: %s", warnings[:8])
    if not clusters:
        raise ValueError("LLM returned no valid cross-connector mapping clusters")
    rationale = (parsed.get("rationale") or "").strip()
    return clusters, rationale
