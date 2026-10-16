"""Map / re-map conversation actions."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional
from uuid import UUID

from agent.tools.map_migrate_tools import _load_extraction_row


def _first_record(row: Dict[str, Any]) -> Dict[str, Any]:
    data = row.get("extracted_data") or row.get("data") or []
    if isinstance(data, list) and data:
        rec = data[0]
        return rec if isinstance(rec, dict) else {}
    if isinstance(data, dict):
        return data
    return {}


async def run_map_action(
    *,
    user_id: str,
    conversation_id: str,
    job_id: Optional[str] = None,
    destination_name: Optional[str] = None,
    destination_entity: Optional[str] = None,
    remap: bool = False,
) -> Dict[str, Any]:
    from services.conversation.session_store import load_or_hydrate_session
    from services.mapping.service import execute_mapping
    from services.mapping_plan.target_resolution import resolve_mapping_target_async
    from services.chat_service import ChatService
    from services.mapping.persist import document_type_for_persist, persist_mapping_execution_result
    from services.conversation.session_sync import sync_session_after_mapping
    from agent.session import AgentSession

    session_snap = await load_or_hydrate_session(user_id, conversation_id)
    resolved_job_id = job_id or session_snap.active_job_id
    if not resolved_job_id:
        raise ValueError("No active extraction job for this conversation.")

    row = await _load_extraction_row(str(resolved_job_id), user_id)
    if not row:
        raise ValueError(f"Extraction job {resolved_job_id} not found.")

    destination = (row.get("destination") or "salesforce").lower()
    if destination == "gsheets":
        destination = "google_sheets"
    dest_name = destination_name or row.get("destination_name")

    flat = _first_record(row)
    filtered = {
        k: v
        for k, v in flat.items()
        if k not in ("Attachments", "_attachments", "attachments")
    }
    if not filtered:
        raise ValueError("No extracted records available for mapping.")

    snap: Dict[str, Any] = dict(getattr(session_snap, "classification_snapshot", None) or {})
    if destination_entity:
        if destination in ("salesforce",):
            snap["destination_object_api_name"] = destination_entity
        elif destination == "sql":
            snap["destination_table_name"] = destination_entity
        elif destination in ("google_sheets", "gsheets"):
            snap["destination_sheet_name"] = destination_entity

    from services.mapping.context import build_mapping_execution_context
    from services.mapping.validate import mapping_config_is_empty

    map_ctx = build_mapping_execution_context(
        classification_snapshot=snap,
        step_args={"destination_entity_key": destination_entity or ""},
        extraction_row=row,
        destination=destination,
    )
    mapping_instruction = map_ctx.mapping_brief or row.get("mapping_context_summary") or row.get("user_prompt")
    target_resolution = None
    if map_ctx.has_pinned_entity:
        target_resolution = map_ctx.to_target_resolution()
    elif mapping_instruction or snap:
        try:
            target_resolution = await resolve_mapping_target_async(
                destination=destination,
                mapping_instruction=mapping_instruction,
                classification_snapshot=snap,
                user_id=UUID(user_id) if not isinstance(user_id, UUID) else user_id,
                destination_name=dest_name,
            )
        except Exception:
            target_resolution = None

    result = await execute_mapping(
        user_id=UUID(user_id) if not isinstance(user_id, UUID) else user_id,
        destination=destination,
        destination_name=dest_name,
        flat_data=filtered,
        extraction_row=row,
        mapping_instruction=mapping_instruction,
        target_resolution=target_resolution,
        is_sql_data=(row.get("source_type") or "").lower() == "sql",
        classification_snapshot=snap,
    )

    if mapping_config_is_empty(result.get("mapping")):
        raise ValueError(
            "No field mappings could be validated for the pinned destination entity."
        )

    mapping_cfg = result.get("mapping") or {}
    mapping_id = f"mapping-{int(time.time() * 1000)}"

    row_meta = row.get("extraction_metadata") or {}
    if isinstance(row_meta, str):
        import json as _json

        try:
            row_meta = _json.loads(row_meta)
        except (_json.JSONDecodeError, TypeError):
            row_meta = {}
    if not isinstance(row_meta, dict):
        row_meta = {}

    def _sql_from_meta(meta: dict) -> Optional[str]:
        for key in ("sql_query", "query", "soql", "SOQL"):
            val = meta.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()
        return None

    sql_query = _sql_from_meta(row_meta)
    source_connector_id = row_meta.get("source_connector_id") or row.get("source_identifier")
    table_name = (
        row_meta.get("table_name")
        or row_meta.get("object_api_name")
        or row.get("source_identifier")
    )
    transformation_rules = result.get("transformation_rules")
    missing_field_preference = result.get("missing_field_preference")
    migration_rules: Dict[str, Any] = {}
    if missing_field_preference:
        migration_rules["missing_field_preference"] = missing_field_preference

    agent_session = AgentSession(
        user_id=user_id,
        conversation_id=conversation_id,
        mode="map",
        active_job_id=str(resolved_job_id),
        destination=destination,
        destination_name=dest_name,
        classification_snapshot=snap,
    )
    saved = await persist_mapping_execution_result(
        agent_session,
        mapping_id=mapping_id,
        destination=destination,
        destination_name=dest_name,
        mapping_cfg=mapping_cfg,
        result=result,
        row=row,
        document_type=document_type_for_persist(
            row=row,
            destination_entity=destination_entity or "",
            destination_document_type=str(
                result.get("destination_document_type")
                or result.get("document_type")
                or ""
            ),
        ),
        migration_rules=migration_rules or None,
        source_connector_id=str(source_connector_id) if source_connector_id else None,
        sql_query=sql_query,
        table_name=str(table_name) if table_name else None,
        sync_session=False,
    )
    if saved:
        mapping_version = saved.mapping_version
    else:
        base_version = int(session_snap.mapping_version or 0)
        mapping_version = base_version + 1 if remap else max(base_version, 0) + 1

    event_data: Dict[str, Any] = {
        "job_id": str(resolved_job_id),
        "mapping_id": mapping_id,
        "destination": destination,
        "destination_name": dest_name,
        "mapping_config": mapping_cfg,
        "mapping_version": mapping_version,
        "field_mappings_count": len(mapping_cfg) if isinstance(mapping_cfg, dict) else 0,
        "document_type": result.get("document_type") or row.get("document_type"),
        "document_description": result.get("document_description") or row.get("document_description"),
        "source_type": row.get("source_type"),
        "source_name": row.get("source_name"),
        "destination_object_api_name": result.get("destination_object_api_name"),
        "mapping_plan": result.get("mapping_plan"),
        "transformation_rules": transformation_rules,
        "source_connector_id": source_connector_id,
        "sql_query": sql_query,
        "table_name": table_name,
    }
    if missing_field_preference:
        event_data["missing_field_preference"] = missing_field_preference

    message = await ChatService.log_data_event(
        conversation_id=conversation_id,
        user_id=user_id,
        event_type="mapping_started",
        event_data=event_data,
    )
    message_id = (message or {}).get("id")

    await sync_session_after_mapping(
        user_id,
        conversation_id,
        job_id=str(resolved_job_id),
        message_id=message_id,
        mapping_version=mapping_version,
        mapping_draft={
            "mapping_config": mapping_cfg,
            **({"transformation_rules": transformation_rules} if transformation_rules else {}),
            **({"missing_field_preference": missing_field_preference} if missing_field_preference else {}),
        },
    )

    return {
        "status": "success",
        "message_id": message_id,
        "mapping_id": mapping_id,
        "mapping_version": mapping_version,
        "job_id": str(resolved_job_id),
        "destination": destination,
        "destination_name": dest_name,
        "mapping_config": mapping_cfg,
        "mapping": result,
        "panel_descriptor": {
            "action": "map_fields_ready",
            **event_data,
        },
    }
