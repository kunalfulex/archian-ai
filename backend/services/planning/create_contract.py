"""Structured create-spreadsheet contract from PLAN_SCOPE + classification."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_create_spreadsheet_contract(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pins title, headers, and header source for google_sheets_create_from_prompt."""
    from services.planning.create_metadata import resolved_spreadsheet_title
    from services.planning.plan_scope import read_plan_scope
    from services.planning.source_column_headers import build_create_header_contract

    g = dict(goal or read_plan_scope(classification))
    title = resolved_spreadsheet_title(classification, g) or (
        (classification.get("planner_new_spreadsheet_name") or "").strip()
        or (g.get("spreadsheet_title") or "").strip()
    )

    ops = [str(o).lower() for o in (g.get("operations") or [])]
    from services.planning.create_metadata import resolve_create_pipeline_tokens

    if not ops:
        ops = resolve_create_pipeline_tokens(classification, g)

    header_contract = build_create_header_contract(classification, g, pipeline_tokens=ops)

    contract: Dict[str, Any] = {
        "spreadsheet_title": title or None,
        "headers": header_contract.headers,
        "use_extraction_headers": header_contract.use_extraction_headers,
        "spreadsheet_header_source": header_contract.spreadsheet_header_source,
        "create_destination": bool(
            classification.get("planner_create_spreadsheet")
            or g.get("create_destination")
            or "create" in ops
        ),
    }
    if header_contract.extraction_job_id:
        contract["extraction_job_id"] = header_contract.extraction_job_id
    conn_id = (
        (classification.get("google_sheets_connection_id") or "").strip()
        or (classification.get("destination_connector_id") or "").strip()
    )
    if conn_id:
        contract["google_sheets_connection_id"] = conn_id
    if classification.get("destination_spreadsheet_id"):
        contract["destination_spreadsheet_id"] = classification.get("destination_spreadsheet_id")
    if classification.get("destination_sheet_name"):
        contract["destination_sheet_name"] = classification.get("destination_sheet_name")
    return contract
