"""Salesforce field mapping executor (schema load → LLM → validate)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.mapping_plan.models import MappingPlanRequest, MappingPlanResult


async def execute_salesforce_field_mapping(
    request: MappingPlanRequest,
    *,
    classification_snapshot: Optional[Dict[str, Any]] = None,
) -> MappingPlanResult:
    """Plan-pinned Salesforce mapping — delegates to handler after resolving connector."""
    from services.mapping_plan.parse_instruction import parse_mapping_instruction, parsed_to_dict
    from services.mapping_plan.validate import validate_mapping_single_primary_object
    from utils.destination_connector_resolve import resolve_salesforce_token_record
    from utils.salesforce_mapping_target import lookup_salesforce_object_in_org

    from handlers.salesforce_handler import map_to_salesforce

    parsed = parse_mapping_instruction(
        request.mapping_instruction,
        request.flat_data,
        destination="salesforce",
    )
    if request.target_object_api_name:
        parsed.target_object_api_name = request.target_object_api_name

    org_id: Optional[str] = None
    token_id = request.destination_connector_id
    dest_name = (request.destination_name or "").strip()

    tr = await resolve_salesforce_token_record(
        request.user_id,
        destination_name=dest_name or None,
        token_id=str(token_id) if token_id else None,
    )
    if tr:
        token_id = str(tr.get("id")) if tr.get("id") else token_id
        org_id = tr.get("org_id")
        if tr.get("org_display_name") and not dest_name:
            dest_name = str(tr.get("org_display_name"))

    if not token_id:
        tr = await resolve_salesforce_token_record(request.user_id)
        if tr:
            token_id = str(tr.get("id")) if tr.get("id") else None
            org_id = org_id or tr.get("org_id")

    plan_pinned = (request.target_resolution_source or "") in (
        "plan_pinned",
        "classification_explicit",
    )

    if parsed.target_object_api_name and org_id:
        meta = await lookup_salesforce_object_in_org(
            request.user_id, org_id, parsed.target_object_api_name
        )
        if meta:
            dest_type = str(meta.get("category") or parsed.target_object_api_name)
            dest_desc = str(meta.get("category_description") or "")
        else:
            dest_type = parsed.target_object_api_name
            dest_desc = ""
    else:
        dest_type = request.source_document_type
        dest_desc = request.source_document_description

    instruction_block = (request.mapping_instruction or "").strip()
    if parsed.target_object_api_name and not plan_pinned:
        instruction_block = (
            f"{instruction_block}\n\n"
            f"CRITICAL: Map ONLY to Salesforce object {parsed.target_object_api_name}. "
            f"Do not map source fields to any other base object."
        ).strip()

    result = await map_to_salesforce(
        flat_data=request.flat_data,
        document_type=dest_type,
        document_description=dest_desc,
        user_id=request.user_id,
        is_sql_data=request.is_sql_data,
        org_id=org_id,
        token_id=token_id,
        destination_name=request.destination_name,
        mapping_context_summary=instruction_block or None,
        explicit_target_object_api_name=request.target_object_api_name,
        plan_pinned_entity=plan_pinned and bool(request.target_object_api_name),
    )

    mapping = result.get("mapping") or {}
    transformation_rules = result.get("transformation_rules") or {"fields": []}

    primary = parsed.target_object_api_name
    if primary:
        validate_mapping_single_primary_object(mapping, primary)

    mapping_audit: Optional[Dict[str, Any]] = None
    try:
        from services.planning.plan_builder import (
            audit_migration_intent_scope,
            merge_intent_issues_into_audit,
            migration_intent_from_snapshot,
        )

        intent = migration_intent_from_snapshot(classification_snapshot)
        if intent:
            intent_issues = audit_migration_intent_scope(
                mapping, request.flat_data, intent
            )
            base_audit = (
                result.get("mapping_audit")
                if isinstance(result.get("mapping_audit"), dict)
                else {}
            )
            mapping_audit = merge_intent_issues_into_audit(base_audit, intent_issues)
    except Exception:
        mapping_audit = None

    dest_api = parsed.target_object_api_name
    dest_doc_type = result.get("document_type") or dest_type
    dest_doc_desc = result.get("document_description") or dest_desc

    return MappingPlanResult(
        mapping=mapping,
        transformation_rules=transformation_rules,
        destination_document_type=dest_doc_type,
        destination_document_description=dest_doc_desc,
        destination_object_api_name=dest_api,
        source_document_type=request.source_document_type,
        source_document_description=request.source_document_description,
        mapping_plan=parsed_to_dict(parsed),
        available_objects=result.get("available_objects"),
        org_rules_display_by_object=result.get("org_rules_display_by_object"),
        connector_id=result.get("connector_id"),
        token_id=result.get("token_id"),
        destination=result.get("destination") or "salesforce",
        destination_name=request.destination_name,
        target_resolution_source=request.target_resolution_source,
        mapping_audit=mapping_audit,
    )
