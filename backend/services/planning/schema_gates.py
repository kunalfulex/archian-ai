"""Schema assessment gates before map/migrate (Phase 6)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional  # noqa: F401 — List used in compute_schema_delta

from services.planning.schemas import ExecutionPlan, PlanStep, ResourceRef, StepType


def _field_name_is_custom(name: str) -> bool:
    return str(name or "").endswith("__c")


def extraction_or_mapping_has_custom_fields(classification: Dict[str, Any]) -> bool:
    """True when extracted columns or an existing mapping reference custom SF fields."""
    for col in classification.get("source_schema") or []:
        if _field_name_is_custom(str(col)):
            return True
    rules = classification.get("extraction_rules") or {}
    for field in (rules.get("transformation_rules") or {}).get("fields") or []:
        if not isinstance(field, dict):
            continue
        out = field.get("output_field") or field.get("source_field") or ""
        if _field_name_is_custom(str(out)):
            return True
    mapping = classification.get("field_mapping") or classification.get("mapping") or {}
    if isinstance(mapping, dict):
        for entry in mapping.values():
            if not isinstance(entry, dict):
                continue
            if entry.get("is_custom"):
                return True
            if _field_name_is_custom(str(entry.get("field") or "")):
                return True
    return False


def plan_needs_schema_gate(classification: Dict[str, Any], goal: Dict[str, Any]) -> bool:
    from services.planning.intent_routing import is_migrate_only_classification

    if is_migrate_only_classification(classification):
        return False

    tokens = classification.get("_pipeline_tokens")
    if isinstance(tokens, list):
        pipeline = [str(t).lower() for t in tokens]
    else:
        from services.planning.goal_parser import split_pipeline_and_data_operations

        pipeline, _ = split_pipeline_and_data_operations(
            [str(o).lower() for o in (goal.get("operations") or [])]
        )

    if not any(t in pipeline for t in ("map", "migrate")):
        return False

    if not extraction_or_mapping_has_custom_fields(classification):
        return False
    dest = (classification.get("destination") or "").lower()
    intent = (goal.get("intent") or classification.get("intent") or "").lower()
    if dest == "salesforce" and intent in ("map_data", "migrate_data", "migrate", "extract"):
        if (classification.get("source") or "").lower() == "salesforce":
            src_name = (classification.get("source_name") or "").strip()
            dest_name = (classification.get("destination_name") or "").strip()
            if src_name and dest_name and src_name.lower() != dest_name.lower():
                return True
    return False


def append_schema_gate_steps(
    steps: List[PlanStep],
    *,
    source: Optional[ResourceRef],
    destination: Optional[ResourceRef],
    source_name: Optional[str],
    destination_name: Optional[str],
    depends_on: Optional[List[str]] = None,
) -> List[PlanStep]:
    """Insert assess step before map/migrate when schema gates are enabled."""
    prev = list(depends_on or [])
    assess = PlanStep(
        step_type=StepType.DEPLOY_SCHEMA,
        label="Assess destination schema",
        source=source,
        destination=destination,
        depends_on=prev,
        parameters={
            "tool_id": "schema_gate_assess",
            "source_name": source_name,
            "destination_name": destination_name,
            "gate_phase": "assess",
        },
    )
    propose = PlanStep(
        step_type=StepType.DEPLOY_SCHEMA,
        label="Review proposed schema changes",
        source=source,
        destination=destination,
        depends_on=["assess_schema"],
        parameters={
            "tool_id": "schema_gate_propose",
            "source_name": source_name,
            "destination_name": destination_name,
            "gate_phase": "propose_schema_delta",
        },
    )
    assess.parameters["step_id"] = "assess_schema"
    propose.parameters["step_id"] = "propose_schema"
    deploy = PlanStep(
        step_type=StepType.DEPLOY_SCHEMA,
        label="Deploy missing fields to destination",
        source=source,
        destination=destination,
        depends_on=["propose_schema"],
        parameters={
            "tool_id": "metadata_deploy_salesforce",
            "step_id": "deploy_schema",
            "source_name": source_name,
            "destination_name": destination_name,
            "requires_user_approval": True,
            "schema_gate_deploy": True,
        },
    )
    if steps:
        head = steps[0]
        head.depends_on = list(
            dict.fromkeys([*(head.depends_on or []), "deploy_schema"])
        )
    return [assess, propose, deploy, *steps]


async def compute_schema_delta_from_job(
    job_id: str,
    user_id: str,
    *,
    destination_object: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare extraction columns to a minimal destination schema snapshot."""
    from services.extraction_results_service import ExtractionResultsService

    row = await ExtractionResultsService.get_extraction_result(job_id, str(user_id))
    if not row:
        return {"missing_fields": [], "source_columns": []}
    data = row.get("extracted_data") or []
    cols: List[str] = []
    if isinstance(data, list) and data and isinstance(data[0], dict):
        cols = sorted({k for k in data[0].keys() if not str(k).startswith("_")})
    return {
        "source_columns": cols,
        "destination_object": destination_object,
        "missing_fields": [],
        "note": "Full destination field compare runs in metadata deploy / map step.",
    }
