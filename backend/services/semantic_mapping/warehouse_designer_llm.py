"""LLM-backed warehouse logical model proposal."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from services.llm import tasks as llm_tasks
from services.semantic_mapping.prompts.warehouse_design_v1 import (
    WAREHOUSE_DESIGN_SYSTEM,
    WAREHOUSE_DESIGN_USER_TEMPLATE,
)
from services.semantic_mapping.schema_bundle import build_schema_bundle, bundle_to_prompt_json
from services.semantic_mapping.warehouse_validator import validate_and_normalize
from services.enterprise_graph.models import GraphMember
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)


async def propose_logical_model_llm(
    *,
    graph_name: str,
    graph_settings: Dict[str, Any],
    members: List[GraphMember],
    canonical_entities: List[Dict[str, Any]],
    canonical_graph: Dict[str, Any],
    semantic_mappings: Optional[List[Dict[str, Any]]] = None,
    dialect: str = "logical",
) -> Tuple[Dict[str, Any], str]:
    """
    Returns (logical_model, rationale_text).
    Raises on LLM/parse failures.
    """
    from services.architect_agent import async_openai_call

    bundle = build_schema_bundle(
        graph_name=graph_name,
        graph_settings=graph_settings,
        members=members,
        canonical_entities=canonical_entities,
        canonical_graph=canonical_graph,
        semantic_mappings=semantic_mappings,
    )
    user_prompt = WAREHOUSE_DESIGN_USER_TEMPLATE.format(
        dialect=dialect,
        modeling_preference=bundle.get("modeling_preference") or "star",
        bundle_json=bundle_to_prompt_json(bundle),
    )

    response = await async_openai_call(
        None,
        task=llm_tasks.EGA_WAREHOUSE_DESIGN,
        messages=[
            {"role": "system", "content": WAREHOUSE_DESIGN_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=8000,
        temperature=0.2,
    )
    raw_text = response.choices[0].message.content.strip()
    parsed = parse_openai_json_response(raw_text)
    logical_model, warnings = validate_and_normalize(parsed, bundle, dialect=dialect)
    rationale = (parsed.get("rationale") or "").strip()
    if warnings:
        logger.info("Warehouse LLM validation warnings: %s", warnings[:5])
    if not logical_model.get("tables"):
        raise ValueError("LLM returned no valid warehouse tables after validation")
    return logical_model, rationale
