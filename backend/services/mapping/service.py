"""Unified mapping orchestrator — dispatch by destination connector."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.mapping.field_map_gs import execute_gsheets_field_mapping
from services.mapping.field_map_sf import execute_salesforce_field_mapping
from services.mapping.field_map_sql import execute_sql_field_mapping
from services.mapping_plan.models import MappingPlanRequest, MappingPlanResult
from services.mapping_plan.target_resolution import MappingTargetResolution


def build_mapping_plan_request(
    *,
    user_id: Any,
    destination: str,
    destination_name: Optional[str],
    flat_data: Dict[str, Any],
    extraction_row: Optional[Dict[str, Any]] = None,
    mapping_instruction: Optional[str] = None,
    target_resolution: Optional[MappingTargetResolution] = None,
    destination_connector_id: Optional[str] = None,
    destination_spreadsheet_id: Optional[str] = None,
    google_sheets_connection_id: Optional[str] = None,
    classification_snapshot: Optional[Dict[str, Any]] = None,
    is_sql_data: bool = False,
) -> MappingPlanRequest:
    row = extraction_row or {}
    meta = row.get("extraction_metadata") or {}
    if isinstance(meta, str):
        import json

        try:
            meta = json.loads(meta)
        except (json.JSONDecodeError, TypeError):
            meta = {}

    snap = classification_snapshot or {}
    src_type = row.get("source_document_type") or row.get("document_type") or ""
    src_desc = row.get("source_document_description") or row.get("document_description") or ""
    dest_type = (destination or "unknown").strip().lower()
    row_source = (row.get("source_type") or "").strip().lower()
    if row_source == "gsheets":
        row_source = "google_sheets"

    dest_connector = destination_connector_id
    dest_spreadsheet_id = (
        (destination_spreadsheet_id or "").strip()
        or (snap.get("destination_spreadsheet_id") or "").strip()
        or (meta.get("destination_spreadsheet_id") or "").strip()
        or None
    )
    gs_connection_id = (
        (google_sheets_connection_id or "").strip()
        or (snap.get("google_sheets_connection_id") or "").strip()
        or (meta.get("google_sheets_connection_id") or "").strip()
        or None
    )

    if dest_type in ("google_sheets", "gsheets"):
        passed = (destination_connector_id or "").strip()
        if passed and not dest_spreadsheet_id:
            dest_spreadsheet_id = passed
        sid = (row.get("source_identifier") or "").strip()
        if not dest_spreadsheet_id and sid and row_source in ("google_sheets", "gsheets"):
            dest_spreadsheet_id = sid
        dest_connector = None
    else:
        if not dest_connector:
            dest_connector = meta.get("destination_connector_id")
            if dest_connector:
                dest_connector = str(dest_connector)
        if not dest_connector:
            tid = meta.get("salesforce_token_id")
            if tid:
                dest_connector = str(tid)
        sid = (row.get("source_identifier") or "").strip()
        if not dest_connector and sid:
            if dest_type == "sql" and row_source == "sql":
                dest_connector = sid

    dest_name = destination_name
    if dest_name and str(dest_name).strip().lower() == "unknown":
        dest_name = None
    if not dest_name and row.get("source_name") and str(row.get("source_type") or "").lower() == "salesforce":
        dest_name = row.get("source_name")

    return MappingPlanRequest(
        user_id=user_id,
        destination=(destination or "unknown").lower(),
        destination_name=dest_name,
        flat_data=flat_data,
        source_document_type=src_type,
        source_document_description=src_desc,
        source_type=row.get("source_type"),
        source_name=row.get("source_name"),
        source_object_api_name=meta.get("source_object_api_name") or row.get("source_identifier"),
        mapping_instruction=(mapping_instruction or "").strip() or None,
        destination_connector_id=dest_connector,
        destination_spreadsheet_id=dest_spreadsheet_id,
        google_sheets_connection_id=gs_connection_id,
        target_object_api_name=(target_resolution.target_object_api_name if target_resolution else None),
        target_table=(target_resolution.target_table if target_resolution else None),
        target_sheet=(target_resolution.target_sheet if target_resolution else None),
        target_resolution_source=(target_resolution.resolution_source if target_resolution else None),
        is_sql_data=is_sql_data,
    )


def mapping_result_to_legacy_dict(result: MappingPlanResult) -> Dict[str, Any]:
    """Shape expected by map_fields API and MapFieldsTool."""
    out: Dict[str, Any] = {
        "destination": result.destination,
        "destination_name": result.destination_name,
        "document_type": result.destination_document_type,
        "document_description": result.destination_document_description,
        "mapping": result.mapping,
        "transformation_rules": result.transformation_rules,
        "source_document_type": result.source_document_type,
        "source_document_description": result.source_document_description,
        "destination_document_type": result.destination_document_type,
        "destination_document_description": result.destination_document_description,
        "destination_object_api_name": result.destination_object_api_name,
        "destination_table": result.destination_table,
        "destination_sheet": result.destination_sheet,
        "mapping_plan": result.mapping_plan,
    }
    if result.available_objects is not None:
        out["available_objects"] = result.available_objects
    if result.available_tables is not None:
        out["available_tables"] = result.available_tables
    if result.available_sheets is not None:
        out["available_sheets"] = result.available_sheets
    if result.connector_id:
        out["connector_id"] = result.connector_id
    if result.token_id:
        out["token_id"] = result.token_id
    if result.spreadsheet_id:
        out["spreadsheet_id"] = result.spreadsheet_id
    if result.primary_table:
        out["primary_table"] = result.primary_table
    if result.org_rules_display_by_object:
        out["org_rules_display_by_object"] = result.org_rules_display_by_object
    if result.target_resolution_source:
        out["target_resolution_source"] = result.target_resolution_source
    if getattr(result, "mapping_audit", None):
        out["mapping_audit"] = result.mapping_audit
    from utils.migration_rules_utils import dual_publish_mapping_transform_api

    dual_publish_mapping_transform_api(out, result.transformation_rules)
    return out


# Backward-compatible alias
mapping_plan_result_to_legacy_dict = mapping_result_to_legacy_dict


async def execute_mapping(
    *,
    user_id: Any,
    destination: str,
    destination_name: Optional[str],
    flat_data: Dict[str, Any],
    extraction_row: Optional[Dict[str, Any]] = None,
    mapping_instruction: Optional[str] = None,
    target_resolution: Optional[MappingTargetResolution] = None,
    destination_connector_id: Optional[str] = None,
    is_sql_data: bool = False,
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from services.planning.plan_builder import enrich_mapping_instruction

    enriched_instruction = enrich_mapping_instruction(
        mapping_instruction,
        classification_snapshot,
    )
    try:
        from services.pipeline_learning.service import PipelineLearningService

        if PipelineLearningService.enabled() and (enriched_instruction or flat_data):
            row = extraction_row or {}
            query_parts = [
                enriched_instruction or "",
                row.get("document_type") or "",
                row.get("source_type") or "",
                destination,
            ]
            ctx = await PipelineLearningService.get_retrieval_context(
                user_id=str(user_id),
                query_text=" ".join(p for p in query_parts if p),
                stage="mapping",
                chunk_type="mapping_template",
                source_type=row.get("source_type"),
                destination_type=destination,
            )
            if ctx:
                enriched_instruction = f"{ctx}\n\n{mapping_instruction or ''}".strip()
    except Exception:
        enriched_instruction = enrich_mapping_instruction(
            mapping_instruction,
            classification_snapshot,
        )

    request = build_mapping_plan_request(
        user_id=user_id,
        destination=destination,
        destination_name=destination_name,
        flat_data=flat_data,
        extraction_row=extraction_row,
        mapping_instruction=enriched_instruction,
        target_resolution=target_resolution,
        destination_connector_id=destination_connector_id,
        classification_snapshot=classification_snapshot,
        is_sql_data=is_sql_data,
    )
    dest = request.destination
    if dest == "salesforce":
        result = await execute_salesforce_field_mapping(
            request,
            classification_snapshot=classification_snapshot,
        )
    elif dest == "sql":
        result = await execute_sql_field_mapping(
            request,
            classification_snapshot=classification_snapshot,
        )
    elif dest in ("google_sheets", "gsheets"):
        result = await execute_gsheets_field_mapping(
            request,
            classification_snapshot=classification_snapshot,
        )
    else:
        raise ValueError(f"Unsupported destination for mapping plan: {dest}")

    return mapping_result_to_legacy_dict(result)
