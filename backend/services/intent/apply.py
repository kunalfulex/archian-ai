"""Apply intent pipeline before and after planner enrichment."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.intent.pipeline import run_intent_pipeline
from services.intent.project import merge_intent_into_classification
from services.intent.types import IntentStepId
from services.planning.resource_catalog import ResourceCatalog


async def apply_intent_pipeline_to_classification(
    classification: Dict[str, Any],
    *,
    prompt: str,
    user_id: str,
    catalog: ResourceCatalog,
    conversation_history: Optional[List[Dict[str, Any]]] = None,
    intent_state_for_classifier: Optional[Dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
    phase: str = "pre_planning",
    start_after_step: Optional[IntentStepId] = None,
    overwrite: Optional[bool] = None,
) -> Dict[str, Any]:
    result = await run_intent_pipeline(
        prompt,
        user_id=str(user_id),
        conversation_id=conversation_id,
        classification=classification,
        catalog=catalog,
        conversation_history=conversation_history,
        intent_state_for_classifier=intent_state_for_classifier,
        phase=phase,
        start_after_step=start_after_step,
    )
    return merge_intent_into_classification(
        classification,
        result.state,
        overwrite=overwrite if overwrite is not None else (phase == "post_planning"),
    )
