"""Persist mapping execution results to conversation_migration_contexts."""
from __future__ import annotations

from typing import Any, Dict, Optional

from agent.session import AgentSession
from services.migration.context import MigrationContext


def document_type_for_persist(
    *,
    row: Dict[str, Any],
    destination_entity: str = "",
    destination_document_type: str = "",
    fallback_document_type: str = "",
) -> str:
    row_dt = str(row.get("document_type") or "").strip()
    if row_dt:
        return row_dt
    pinned = (destination_entity or "").strip()
    if pinned:
        return pinned
    return str(destination_document_type or fallback_document_type or "").strip()


def resolve_sheet_name_for_persist(
    *,
    destination: str,
    result: Dict[str, Any],
    classification_snapshot: Optional[Dict[str, Any]] = None,
    preferred_sheet: Optional[str] = None,
) -> Optional[str]:
    """Pick destination tab for GSheets persist (plan pin → available_sheets → first)."""
    if preferred_sheet:
        return preferred_sheet
    if destination not in ("google_sheets", "gsheets"):
        return None

    avail = result.get("available_sheets") or {}
    if not isinstance(avail, dict) or not avail:
        return None

    snap = classification_snapshot or {}
    tab = (snap.get("destination_sheet_name") or "").strip().lower()
    if tab:
        for name in avail.keys():
            if str(name).strip().lower() == tab:
                return str(name)
    return str(next(iter(avail.keys()), "")) or None


async def persist_mapping_execution_result(
    session: AgentSession,
    *,
    mapping_id: str,
    destination: str,
    destination_name: Optional[str],
    mapping_cfg: Dict[str, Any],
    result: Dict[str, Any],
    row: Dict[str, Any],
    document_type: str,
    destination_entity: str = "",
    sheet_name: Optional[str] = None,
    connector_id: Optional[str] = None,
    table_name: Optional[str] = None,
    migration_rules: Optional[Dict[str, Any]] = None,
    source_connector_id: Optional[str] = None,
    sql_query: Optional[str] = None,
    sync_session: bool = True,
) -> Optional[MigrationContext]:
    """
    Upsert canonical mapping context after execute_mapping.

    All agentic map paths (MapFieldsTool, run_map_action) should call this.
    """
    del destination_entity  # reserved for callers building document_type

    if not isinstance(mapping_cfg, dict) or not mapping_cfg:
        return None
    if not session.conversation_id or not session.active_job_id:
        return None

    from services.migration.session_migration import persist_session_migration_context
    from services.migration.transform_state import has_mapping_transform_rules
    from services.migration.write_policy import resolve_write_policy

    transform_rules = result.get("transformation_rules") or result.get("mapping_transformation")
    resolved_sheet = resolve_sheet_name_for_persist(
        destination=destination,
        result=result,
        classification_snapshot=session.classification_snapshot,
        preferred_sheet=sheet_name,
    )
    write_policy = resolve_write_policy(destination=destination, mapping=mapping_cfg)

    saved = await persist_session_migration_context(
        session,
        mapping=mapping_cfg,
        document_type=document_type,
        destination_object_api_name=result.get("destination_object_api_name"),
        mapping_id=mapping_id,
        destination=destination,
        destination_name=destination_name,
        document_description=result.get("destination_document_description")
        or result.get("document_description"),
        connector_id=connector_id or result.get("connector_id") or result.get("token_id"),
        table_name=table_name or result.get("primary_table"),
        spreadsheet_id=result.get("spreadsheet_id"),
        spreadsheet_name=result.get("destination_name") or destination_name,
        sheet_name=resolved_sheet,
        source_type=row.get("source_type"),
        source_connector_id=source_connector_id,
        sql_query=sql_query,
        transformation_rules=transform_rules,
        migration_rules=migration_rules,
        operation=write_policy.operation,
        mapping_config_transform_applied=has_mapping_transform_rules(transform_rules),
    )

    if saved and sync_session:
        from services.conversation.session_sync import sync_session_after_mapping

        await sync_session_after_mapping(
            session.user_id,
            session.conversation_id,
            job_id=str(session.active_job_id),
            mapping_version=saved.mapping_version,
            mapping_draft={
                "mapping_config": dict(mapping_cfg),
                "transformation_rules": transform_rules,
            },
        )
    return saved
