"""Google Sheets field mapping executor (plan-pinned tab → LLM → validate)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.mapping_plan.models import MappingPlanRequest, MappingPlanResult


async def execute_gsheets_field_mapping(
    request: MappingPlanRequest,
    *,
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> MappingPlanResult:
    from handlers.google_sheets_handler import map_to_google_sheets
    from services.mapping_plan.parse_instruction import parse_mapping_instruction, parsed_to_dict

    del classification_snapshot  # reserved for intent audit parity

    parsed = parse_mapping_instruction(
        request.mapping_instruction,
        request.flat_data,
        destination="google_sheets",
    )
    if request.target_sheet:
        parsed.target_sheet = request.target_sheet

    plan_pinned = (request.target_resolution_source or "") in (
        "plan_pinned",
        "classification_explicit",
    )
    pinned_sheet = (parsed.target_sheet or request.target_sheet or "").strip()

    instruction = (request.mapping_instruction or "").strip()
    if pinned_sheet and not plan_pinned:
        instruction = (
            f"{instruction}\n\nMap to sheet {pinned_sheet} only."
        ).strip()

    dest_type = request.source_document_type
    dest_desc = request.source_document_description

    result = await map_to_google_sheets(
        flat_data=request.flat_data,
        document_type=dest_type,
        document_description=dest_desc,
        destination_name=request.destination_name or "",
        user_id=request.user_id,
        mapping_context_summary=instruction or None,
        destination_sheet_name=pinned_sheet or None,
        spreadsheet_id=request.destination_spreadsheet_id,
        google_sheets_connection_id=request.google_sheets_connection_id,
        plan_pinned_entity=plan_pinned and bool(pinned_sheet),
    )

    return MappingPlanResult(
        mapping=result.get("mapping") or {},
        transformation_rules=result.get("transformation_rules") or {"fields": []},
        destination_document_type=result.get("document_type") or dest_type,
        destination_document_description=result.get("document_description") or dest_desc,
        destination_sheet=pinned_sheet or result.get("destination_sheet"),
        source_document_type=request.source_document_type,
        source_document_description=request.source_document_description,
        mapping_plan=parsed_to_dict(parsed),
        available_sheets=result.get("available_sheets"),
        spreadsheet_id=result.get("spreadsheet_id"),
        destination=result.get("destination") or "google_sheets",
        destination_name=request.destination_name,
        target_resolution_source=request.target_resolution_source,
    )
