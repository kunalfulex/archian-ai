"""Re-run intent pipeline after user answers a clarification."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from services.intent.clarification_types import CONNECTOR_GAP_TYPES, ENTITY_FIELD_GAP_TYPES
from services.intent.types import IntentStepId
from services.planning.resource_catalog import ResourceCatalog


_FIELD_GAP_TYPES = frozenset({"pick_extract_fields", "migration_intent_separate_pipelines"})

RESUME_STEP_BY_TYPE: Dict[str, IntentStepId] = {
    "pick_extract_fields": IntentStepId.S08_FIELD_SCOPE,
    "migration_intent_separate_pipelines": IntentStepId.S08_FIELD_SCOPE,
    "pick_source_entity": IntentStepId.S07_ENTITY,
    "pick_destination_entity": IntentStepId.S07_ENTITY,
    "migration_intent_entity_ambiguity": IntentStepId.S07_ENTITY,
    "pick_source_connector": IntentStepId.S06_BIND_RESOURCES,
    "pick_destination_connector": IntentStepId.S06_BIND_RESOURCES,
    "pick_salesforce_org": IntentStepId.S06_BIND_RESOURCES,
    "pick_google_account": IntentStepId.S06_BIND_RESOURCES,
    "pick_sql_connector": IntentStepId.S06_BIND_RESOURCES,
    "pick_workbook": IntentStepId.S06_BIND_RESOURCES,
    "pick_workbook_ambiguous": IntentStepId.S06_BIND_RESOURCES,
    "pick_connector_by_label": IntentStepId.S06_BIND_RESOURCES,
    "pick_tab": IntentStepId.S06_BIND_RESOURCES,
    "create_workbook_name": IntentStepId.S06_BIND_RESOURCES,
}


def resume_phase_for_clarification(clarification_type: str) -> str:
    """Return pre_planning for connector gaps, post_planning for entity/field gaps."""
    ctype = (clarification_type or "").strip()
    if ctype in CONNECTOR_GAP_TYPES:
        return "pre_planning"
    if ctype in ENTITY_FIELD_GAP_TYPES or ctype in _FIELD_GAP_TYPES:
        return "post_planning"
    return "post_planning"


def resume_step_after_clarification(clarification_type: str) -> IntentStepId:
    """First pipeline step to re-run after the user resolves this gap."""
    ctype = (clarification_type or "").strip()
    return RESUME_STEP_BY_TYPE.get(
        ctype,
        IntentStepId.S06_BIND_RESOURCES if ctype in CONNECTOR_GAP_TYPES else IntentStepId.S07_ENTITY,
    )


def primary_clarification_type(selection_types: Sequence[str]) -> Optional[str]:
    for ctype in selection_types or []:
        if (ctype or "").strip():
            return str(ctype).strip()
    return None


async def resume_intent_after_clarification(
    classification: Dict[str, Any],
    *,
    prompt: str,
    user_id: str,
    catalog: ResourceCatalog,
    selection_types: Optional[Sequence[str]] = None,
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-run intent pipeline from the step after the answered clarification.

    Called after apply_clarification_selections and before advance_planner_pipeline.
    """
    from services.intent.apply import apply_intent_pipeline_to_classification

    ctype = primary_clarification_type(list(selection_types or []))
    if not ctype:
        return dict(classification or {})

    phase = resume_phase_for_clarification(ctype)
    start_after = resume_step_after_clarification(ctype)

    return await apply_intent_pipeline_to_classification(
        dict(classification or {}),
        prompt=prompt,
        user_id=str(user_id),
        catalog=catalog,
        conversation_id=conversation_id,
        phase=phase,
        start_after_step=start_after,
        overwrite=(phase == "post_planning"),
    )
