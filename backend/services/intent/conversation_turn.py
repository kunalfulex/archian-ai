"""Early exit for general chat and schema/info turns (no S01, classifier, or planner)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from services.planning.resource_catalog import ResourceCatalog

logger = logging.getLogger(__name__)

_PIPELINE_FIELDS = (
    "operations",
    "execution_plan",
    "plan_status",
    "plan_id",
    "_continuation_steps",
    "active_job_id",
    "needs_clarification",
    "clarifications",
    "plan_steps_summary",
    "session_action",
    "continuation_phase",
    "_session_continuation",
    "_session_progress",
)


def _strip_pipeline_fields(out: Dict[str, Any]) -> Dict[str, Any]:
    for key in _PIPELINE_FIELDS:
        out.pop(key, None)
    return out


async def enrich_info_turn(
    prompt: str,
    *,
    user_id: str,
    catalog: ResourceCatalog,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve connector for schema/info questions; skip planner pipeline."""
    from services.planning.intent_routing import enrich_info_query_classification

    out: Dict[str, Any] = {
        "intent": "info",
        "mode": "general",
        "plan_route": "info_path",
        "confidence": 1.0,
        "reasoning": "Schema or resource info query",
        "original_prompt": (prompt or "").strip() or None,
    }
    out = await enrich_info_query_classification(
        out,
        prompt,
        catalog,
        user_id=str(user_id),
    )
    out = _strip_pipeline_fields(out)
    logger.info(
        "[classify_and_enrich] info_path source=%s source_name=%s",
        out.get("source"),
        out.get("source_name"),
    )
    return out


async def enrich_general_turn(
    prompt: str,
    *,
    user_id: str,
    conversation_id: Optional[str] = None,
    conversational_turn: Optional[bool] = None,
    off_topic: Optional[bool] = None,
) -> Dict[str, Any]:
    """Greeting, capability, or off-topic — direct to general chat."""
    out: Dict[str, Any] = {
        "intent": "general",
        "mode": "general",
        "plan_route": "general_path",
        "confidence": 1.0,
        "reasoning": "General conversation turn",
        "original_prompt": (prompt or "").strip() or None,
    }
    if conversational_turn is True:
        out["conversational_turn"] = True
    if off_topic is True:
        out["off_topic"] = True
    out = _strip_pipeline_fields(out)
    logger.info(
        "[classify_and_enrich] general_path conversational=%s off_topic=%s",
        out.get("conversational_turn"),
        out.get("off_topic"),
    )
    return out
