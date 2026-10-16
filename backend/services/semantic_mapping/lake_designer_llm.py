"""LLM-first medallion lake layout proposal."""
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
from services.semantic_mapping.lake_validator import validate_lake_logical_model
from services.semantic_mapping.prompts.lake_design_v1 import (
    LAKE_DESIGN_SYSTEM,
    LAKE_DESIGN_USER_TEMPLATE,
)
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)


async def propose_lake_logical_model_llm(
    *,
    graph_name: str,
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    inventory: List[SourceEntity],
    user_id: str,
    semantic_mappings: List[Dict[str, Any]],
    canonical_entities: List[Dict[str, Any]],
    dialect: str,
    bronze_schema: str,
    silver_schema: str,
) -> Tuple[Dict[str, Any], str]:
    """Returns (logical_model, rationale). Raises on failure."""
    from services.architect_agent import async_openai_call

    bundle = await build_ega_intelligence_bundle(
        graph_name=graph_name,
        graph_settings=graph_settings,
        members=members,
        inventory=inventory,
        user_id=user_id,
        semantic_mappings=semantic_mappings,
        canonical_entities=canonical_entities,
    )
    user_prompt = LAKE_DESIGN_USER_TEMPLATE.format(
        dialect=dialect,
        bronze_schema=bronze_schema,
        silver_schema=silver_schema,
        bundle_json=bundle_to_prompt_json(bundle),
    )
    response = await async_openai_call(
        None,
        task=llm_tasks.EGA_LAKE_DESIGN,
        messages=[
            {"role": "system", "content": LAKE_DESIGN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=8000,
        temperature=0.2,
    )
    parsed = parse_openai_json_response(response.choices[0].message.content.strip())
    logical_model, warnings = validate_lake_logical_model(
        parsed,
        inventory,
        members=members,
        bronze_schema=bronze_schema,
        silver_schema=silver_schema,
        dialect=dialect,
    )
    if warnings:
        logger.info("Lake LLM validation warnings: %s", warnings[:8])
    rationale = (parsed.get("rationale") or "").strip()
    return logical_model, rationale
