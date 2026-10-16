"""SQL field mapping executor (plan-pinned table → LLM → validate)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.mapping_plan.models import MappingPlanRequest, MappingPlanResult


async def execute_sql_field_mapping(
    request: MappingPlanRequest,
    *,
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> MappingPlanResult:
    from handlers.sql_handler import map_to_database
    from services.mapping_plan.parse_instruction import parse_mapping_instruction, parsed_to_dict

    del classification_snapshot  # reserved for intent audit parity

    parsed = parse_mapping_instruction(
        request.mapping_instruction,
        request.flat_data,
        destination="sql",
    )
    if request.target_table:
        parsed.target_table = request.target_table

    plan_pinned = (request.target_resolution_source or "") in (
        "plan_pinned",
        "classification_explicit",
    )
    pinned_table = (parsed.target_table or request.target_table or "").strip()

    instruction = (request.mapping_instruction or "").strip()
    if pinned_table and not plan_pinned:
        instruction = (
            f"{instruction}\n\nMap to SQL table {pinned_table} only."
        ).strip()

    dest_type = request.source_document_type
    dest_desc = request.source_document_description

    result = await map_to_database(
        flat_data=request.flat_data,
        document_type=dest_type,
        document_description=dest_desc,
        destination_name=request.destination_name or "",
        user_id=request.user_id,
        mapping_context_summary=instruction or None,
        destination_connector_id=request.destination_connector_id,
        explicit_target_table=pinned_table or None,
        plan_pinned_entity=plan_pinned and bool(pinned_table),
    )

    mapping = result.get("mapping") or {}

    return MappingPlanResult(
        mapping=mapping,
        transformation_rules=result.get("transformation_rules") or {"fields": []},
        destination_document_type=result.get("document_type") or dest_type,
        destination_document_description=result.get("document_description") or dest_desc,
        destination_table=pinned_table or result.get("primary_table"),
        source_document_type=request.source_document_type,
        source_document_description=request.source_document_description,
        mapping_plan=parsed_to_dict(parsed),
        available_tables=result.get("available_tables"),
        connector_id=result.get("connector_id"),
        destination=result.get("destination") or "sql",
        destination_name=request.destination_name,
        primary_table=result.get("primary_table"),
        target_resolution_source=request.target_resolution_source,
    )
