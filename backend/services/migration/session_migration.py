"""Load and apply migration context onto AgentSession."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Optional, Union

from agent.session import AgentSession
from services.migration.context import MigrationContext
from services.migration.context_repository import get_context, upsert_context


async def hydrate_session_migration_context(session: AgentSession) -> None:
    if not session.conversation_id or not session.active_job_id:
        return
    ctx = await get_context(
        user_id=session.user_id,
        conversation_id=session.conversation_id,
        extraction_job_id=session.active_job_id,
    )
    if ctx:
        apply_context_to_session(session, ctx)


def apply_context_to_session(session: AgentSession, ctx: MigrationContext) -> None:
    session.mapping_id = ctx.mapping_id
    session.mapping_config = dict(ctx.mapping)
    session.mapping_version = ctx.mapping_version
    from services.planning.conversation_session import coerce_destination_type

    snap = session.classification_snapshot or {}
    approved_name = (snap.get("destination_name") or "").strip()
    approved_dest = coerce_destination_type(snap.get("destination"), default="")
    planner_confirmed = bool(snap.get("planner_destination_confirmed"))
    ctx_name = (ctx.destination_name or "").strip()
    ctx_dest = coerce_destination_type(ctx.destination, default="")

    if ctx.destination:
        if not (planner_confirmed and approved_dest and ctx_dest and ctx_dest != approved_dest):
            session.destination = ctx.destination
    if ctx.destination_name:
        if not (
            planner_confirmed
            and approved_name
            and ctx_name
            and approved_name.lower() != ctx_name.lower()
        ):
            session.destination_name = ctx.destination_name


async def persist_session_migration_context(
    session: AgentSession,
    *,
    mapping: Dict[str, Any],
    document_type: str,
    mapping_id: Optional[str] = None,
    destination: Optional[str] = None,
    destination_name: Optional[str] = None,
    destination_object_api_name: Optional[str] = None,
    document_description: Optional[str] = None,
    is_transformed_data: bool = False,
    transformation_rules: Optional[Dict[str, Any]] = None,
    migration_rules: Optional[Dict[str, Any]] = None,
    connector_id: Optional[str] = None,
    table_name: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    spreadsheet_name: Optional[str] = None,
    sheet_name: Optional[str] = None,
    operation: Optional[str] = None,
    source_connector_id: Optional[str] = None,
    sql_query: Optional[str] = None,
    source_type: Optional[str] = None,
    extracted_schema_type: Optional[str] = None,
    mapping_config_transform_applied: bool = False,
) -> Optional[MigrationContext]:
    if not session.conversation_id or not session.active_job_id:
        return None
    dest = _normalize_destination(destination or session.destination)
    from utils.migration_destination_entity import coerce_destination_entity_api_name

    resolved_entity = coerce_destination_entity_api_name(
        destination_object_api_name,
        connector_type=dest,
        mapping=mapping,
    )
    ctx = MigrationContext(
        user_id=session.user_id,
        extraction_job_id=session.active_job_id,
        mapping=dict(mapping),
        document_type=document_type,
        destination_object_api_name=resolved_entity,
        destination=dest,
        destination_name=destination_name or session.destination_name,
        document_description=document_description,
        conversation_id=session.conversation_id,
        mapping_id=mapping_id,
        is_transformed_data=is_transformed_data,
        transformation_rules=transformation_rules,
        migration_rules=migration_rules or {},
        connector_id=connector_id,
        table_name=table_name,
        spreadsheet_id=spreadsheet_id,
        spreadsheet_name=spreadsheet_name,
        sheet_name=sheet_name,
        operation=operation,
        source_connector_id=source_connector_id,
        sql_query=sql_query,
        source_type=source_type,
        extracted_schema_type=extracted_schema_type,
        mapping_config_transform_applied=mapping_config_transform_applied,
    )
    saved = await upsert_context(ctx)
    apply_context_to_session(session, saved)
    try:
        from services.extraction_results_service import ExtractionResultsService

        await ExtractionResultsService.update_pipeline_stage(
            session.active_job_id,
            session.user_id,
            "mapped",
        )
    except Exception:
        pass
    return saved


def _mapping_from_chat_meta(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mapping_cfg = meta.get("mapping_config") or meta.get("mapping")
    if isinstance(mapping_cfg, dict) and mapping_cfg.get("mapping"):
        mapping_cfg = mapping_cfg.get("mapping")
    if isinstance(mapping_cfg, dict) and mapping_cfg:
        return dict(mapping_cfg)
    return None


def _apply_stored_mapping_artifacts(
    ctx: MigrationContext,
    stored: MigrationContext,
) -> None:
    if stored.mapping_id:
        ctx.mapping_id = stored.mapping_id
    if stored.mapping_version:
        ctx.mapping_version = stored.mapping_version
    if stored.document_type and not ctx.document_type:
        ctx.document_type = stored.document_type
    if stored.destination_object_api_name and not ctx.destination_object_api_name:
        ctx.destination_object_api_name = stored.destination_object_api_name
    if stored.destination_name and not ctx.destination_name:
        ctx.destination_name = stored.destination_name
    if stored.destination and ctx.destination in ("", "salesforce", "unknown"):
        ctx.destination = stored.destination
    if stored.transformation_rules and isinstance(stored.transformation_rules, dict):
        ctx.transformation_rules = stored.transformation_rules
    if stored.migration_rules and isinstance(stored.migration_rules, dict):
        ctx.migration_rules = dict(stored.migration_rules)
    if stored.table_name and not ctx.table_name:
        ctx.table_name = stored.table_name
    if stored.connector_id and not ctx.connector_id:
        ctx.connector_id = stored.connector_id


def _merge_http_transport_fields(
    canonical: MigrationContext,
    http: MigrationContext,
) -> MigrationContext:
    """Keep canonical mapping; fill connector/transport gaps from the HTTP request."""
    merged = replace(
        canonical,
        extraction_job_id=http.extraction_job_id or canonical.extraction_job_id,
        is_transformed_data=http.is_transformed_data or canonical.is_transformed_data,
        record_ordinals=http.record_ordinals or canonical.record_ordinals,
        parent_batch_id=http.parent_batch_id or canonical.parent_batch_id,
    )
    if http.spreadsheet_id and not merged.spreadsheet_id:
        merged = replace(merged, spreadsheet_id=http.spreadsheet_id)
    if http.sheet_name and not merged.sheet_name:
        merged = replace(merged, sheet_name=http.sheet_name)
    if http.spreadsheet_name and not merged.spreadsheet_name:
        merged = replace(merged, spreadsheet_name=http.spreadsheet_name)
    if http.connector_id and not merged.connector_id:
        merged = replace(merged, connector_id=http.connector_id)
    if http.table_name and not merged.table_name:
        merged = replace(merged, table_name=http.table_name)
    if http.operation and not merged.operation:
        merged = replace(merged, operation=http.operation)
    if http.source_connector_id and not merged.source_connector_id:
        merged = replace(merged, source_connector_id=http.source_connector_id)
    if http.sql_query and not merged.sql_query:
        merged = replace(merged, sql_query=http.sql_query)
    if http.source_type and not merged.source_type:
        merged = replace(merged, source_type=http.source_type)
    if http.extracted_schema_type and not merged.extracted_schema_type:
        merged = replace(merged, extracted_schema_type=http.extracted_schema_type)
    if http.document_description and not merged.document_description:
        merged = replace(merged, document_description=http.document_description)
    http_dest = _normalize_destination(http.destination) if http.destination else ""
    canon_dest = _normalize_destination(merged.destination) if merged.destination else ""
    if http_dest and http_dest not in ("unknown", "") and http_dest != canon_dest:
        merged = replace(
            merged,
            destination=http_dest,
            destination_name=http.destination_name or merged.destination_name,
        )
    elif http.destination_name and not merged.destination_name:
        merged = replace(merged, destination_name=http.destination_name)
    if http.migration_rules:
        rules = dict(merged.migration_rules or {})
        rules.update(http.migration_rules)
        merged = replace(merged, migration_rules=rules)
    return merged


async def _load_canonical_migration_context(
    user_id: str,
    conversation_id: str,
    extraction_job_id: str,
) -> Optional[MigrationContext]:
    """Latest mapping from conversation_migration_contexts (direct row or lineage)."""
    db_ctx = await get_context(
        user_id=user_id,
        conversation_id=conversation_id,
        extraction_job_id=extraction_job_id,
    )
    if db_ctx and db_ctx.mapping:
        return db_ctx

    from services.migration.mapping_lineage import find_lineage_mapping_context

    found = await find_lineage_mapping_context(
        user_id=user_id,
        conversation_id=conversation_id,
        target_job_id=extraction_job_id,
    )
    if found:
        stored_ctx, _mapping_job_id = found
        if stored_ctx.mapping:
            return stored_ctx
    return None


async def resolve_migration_context_for_migrate(
    ctx: MigrationContext,
    *,
    session: Optional[AgentSession] = None,
) -> MigrationContext:
    """
    Canonical mapping for migrate: DB context is authoritative when all writers sync to it.

    HTTP transport fields (spreadsheet_id, connector_id, etc.) backfill gaps on the DB row.
    """
    job_id = str(ctx.extraction_job_id or "").strip()
    if not job_id:
        return ctx

    http_ctx = ctx
    canonical: Optional[MigrationContext] = None

    if ctx.conversation_id:
        canonical = await _load_canonical_migration_context(
            ctx.user_id,
            ctx.conversation_id,
            job_id,
        )

    if canonical and canonical.mapping:
        merged = _merge_http_transport_fields(canonical, http_ctx)
        merged.extraction_job_id = job_id
        if session is not None:
            merged = await enrich_migration_context(merged, session, job_id)
            merged.extraction_job_id = job_id
            apply_context_to_session(session, merged)
        return merged

    if session is not None:
        tool_ctx = await resolve_migration_context_for_tool(session, job_id)
        if tool_ctx and tool_ctx.mapping:
            merged = _merge_http_transport_fields(tool_ctx, http_ctx)
            merged.extraction_job_id = job_id
            return merged

    return http_ctx


async def resolve_migration_context_for_tool(
    session: AgentSession,
    job_id: str,
) -> Optional[MigrationContext]:
    """DB (target + lineage) → session → chat history fallback."""
    target_job_id = str(job_id or "").strip()
    if not target_job_id:
        return None

    ctx: Optional[MigrationContext] = None

    if session.conversation_id:
        db_ctx = await get_context(
            user_id=session.user_id,
            conversation_id=session.conversation_id,
            extraction_job_id=target_job_id,
        )
        if db_ctx and db_ctx.mapping:
            ctx = _context_from_session(
                session,
                target_job_id,
                dict(db_ctx.mapping),
                destination=db_ctx.destination,
                destination_name=db_ctx.destination_name,
            )
            _apply_stored_mapping_artifacts(ctx, db_ctx)
            apply_context_to_session(session, ctx)

    if ctx is None and session.conversation_id:
        from services.migration.mapping_lineage import find_lineage_mapping_context

        found = await find_lineage_mapping_context(
            user_id=session.user_id,
            conversation_id=session.conversation_id,
            target_job_id=target_job_id,
        )
        if found:
            stored_ctx, _mapping_job_id = found
            ctx = _context_from_session(
                session,
                target_job_id,
                dict(stored_ctx.mapping),
                destination=stored_ctx.destination,
                destination_name=stored_ctx.destination_name,
            )
            _apply_stored_mapping_artifacts(ctx, stored_ctx)

    if ctx is None:
        mapping_config = session.mapping_config
        if isinstance(mapping_config, dict) and mapping_config:
            ctx = _context_from_session(session, target_job_id, mapping_config)

    if ctx is None:
        from services.migration.mapping_lineage import is_lineage_ancestor
        from services.planning.conversation_session import latest_mapping_started_metadata

        meta = latest_mapping_started_metadata(session.conversation_history)
        if meta:
            meta_job = (meta.get("job_id") or "").strip()
            mapping_cfg = _mapping_from_chat_meta(meta)
            meta_ok = not meta_job or meta_job == target_job_id
            if not meta_ok and meta_job:
                meta_ok = await is_lineage_ancestor(
                    session.user_id, meta_job, target_job_id
                )
            if mapping_cfg and (not target_job_id or not meta_job or meta_ok):
                ctx = _context_from_session(
                    session,
                    target_job_id,
                    mapping_cfg,
                    destination=meta.get("destination"),
                    destination_name=meta.get("destination_name"),
                )
                ctx.mapping_id = meta.get("mapping_id")
                _enrich_context_from_meta(ctx, meta)

    if ctx is None:
        return None

    ctx.extraction_job_id = target_job_id
    ctx = await enrich_migration_context(ctx, session, target_job_id)
    ctx.extraction_job_id = target_job_id
    return ctx


def _agent_session_from_conversation(
    user_id: str,
    conversation_id: str,
    job_id: str,
    conv_session,
) -> AgentSession:
    from services.planning.conversation_session import coerce_destination_type

    intent = conv_session.intent_state or {}
    draft = conv_session.mapping_draft or {}
    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    raw_dest = (
        intent.get("active_destination")
        or intent.get("destination_type")
        or intent.get("destination")
    )
    destination_name = intent.get("destination_name")
    dest_obj = intent.get("destination")
    if not destination_name and isinstance(dest_obj, dict):
        destination_name = dest_obj.get("display_name") or dest_obj.get("name")
    return AgentSession(
        user_id=user_id,
        conversation_id=conversation_id,
        mode="migrate",
        active_job_id=job_id,
        destination=coerce_destination_type(raw_dest, default=""),
        destination_name=destination_name,
        classification_snapshot=intent,
        mapping_config=mapping_cfg if isinstance(mapping_cfg, dict) else None,
        mapping_version=conv_session.mapping_version,
    )


async def resolve_migration_context_for_execution(
    *,
    user_id: str,
    conversation_id: str,
    job_id: str,
    session: Optional[AgentSession] = None,
    mapping_message_id: Optional[str] = None,
) -> Optional[MigrationContext]:
    """
    Flush session mapping draft to chat + DB, then resolve canonical migration context.
    """
    from services.conversation.actions.mapping_draft import flush_mapping_draft_to_context
    from services.conversation.session_store import load_or_hydrate_session

    await flush_mapping_draft_to_context(
        user_id,
        conversation_id,
        job_id=job_id,
        mapping_message_id=mapping_message_id,
    )

    if session is None:
        conv = await load_or_hydrate_session(user_id, conversation_id)
        session = _agent_session_from_conversation(user_id, conversation_id, job_id, conv)

    ctx = await resolve_migration_context_for_tool(session, job_id)
    if ctx:
        ctx.conversation_id = conversation_id
    return ctx


async def enrich_migration_context(
    ctx: MigrationContext,
    session: AgentSession,
    job_id: str,
) -> MigrationContext:
    """Fill destination connector gaps from session, extraction row, DB, and lookups."""
    dest = _normalize_destination(ctx.destination or session.destination)
    ctx.destination = dest

    _apply_session_hints(ctx, _session_hints(session), dest)

    from services.extraction_results_service import ExtractionResultsService

    row = await ExtractionResultsService.get_extraction_result(
        job_id,
        session.user_id,
        prefer_lineage_latest=False,
    )
    if row:
        _apply_extraction_row_hints(ctx, row, dest)

    if session.conversation_id:
        db_ctx = await get_context(
            user_id=session.user_id,
            conversation_id=session.conversation_id,
            extraction_job_id=job_id,
        )
        if db_ctx:
            _merge_persisted_context(ctx, db_ctx)

    if dest == "sql":
        await _enrich_sql_destination(ctx, session)
    elif dest == "google_sheets":
        await _enrich_gsheets_destination(ctx, session)
    elif dest == "salesforce":
        await _enrich_salesforce_destination(ctx, session)

    _apply_recovery_scope_from_snapshot(ctx, session.classification_snapshot or {})
    return ctx


def _apply_recovery_scope_from_snapshot(
    ctx: MigrationContext,
    snap: Dict[str, Any],
) -> None:
    if not snap.get("recovery_approved"):
        return
    ordinals = snap.get("recovery_record_ordinals") or snap.get("recoverable_record_indices")
    if ordinals:
        ctx.record_ordinals = [int(o) for o in ordinals]
        rules = dict(ctx.migration_rules or {})
        rules["record_ordinals"] = ctx.record_ordinals
        ctx.migration_rules = rules
    parent_batch = snap.get("recovery_parent_batch_id") or snap.get("recovery_batch_id")
    if parent_batch:
        ctx.parent_batch_id = str(parent_batch)
        rules = dict(ctx.migration_rules or {})
        rules["parent_batch_id"] = ctx.parent_batch_id
        ctx.migration_rules = rules


def _normalize_destination(dest: Union[str, Dict[str, Any], None]) -> str:
    from services.planning.conversation_session import coerce_destination_type

    return coerce_destination_type(dest, default="salesforce")


def _session_hints(session: AgentSession) -> Dict[str, Any]:
    last = session.last_result or {}
    snap = session.classification_snapshot or {}
    return {
        "connector_id": (
            last.get("destination_connector_id")
            or last.get("connector_id")
            or last.get("token_id")
            or snap.get("destination_connector_id")
        ),
        "table_name": (
            last.get("primary_table")
            or snap.get("destination_table_name")
            or snap.get("primary_table")
        ),
        "spreadsheet_id": (
            last.get("spreadsheet_id")
            or snap.get("destination_spreadsheet_id")
        ),
        "sheet_name": (
            last.get("sheet_name")
            or last.get("destination_sheet_name")
            or snap.get("destination_sheet_name")
        ),
        "source_connector_id": last.get("source_connector_id"),
        "source_type": last.get("source_type"),
    }


def _apply_session_hints(
    ctx: MigrationContext,
    hints: Dict[str, Any],
    dest: str,
) -> None:
    if dest == "sql":
        ctx.connector_id = ctx.connector_id or hints.get("connector_id")
        ctx.table_name = ctx.table_name or hints.get("table_name")
    elif dest == "google_sheets":
        ctx.spreadsheet_id = ctx.spreadsheet_id or hints.get("spreadsheet_id")
        ctx.sheet_name = ctx.sheet_name or hints.get("sheet_name")
    elif dest == "salesforce":
        ctx.connector_id = ctx.connector_id or hints.get("connector_id")

    ctx.source_connector_id = ctx.source_connector_id or hints.get("source_connector_id")
    ctx.source_type = ctx.source_type or hints.get("source_type")


def _apply_extraction_row_hints(
    ctx: MigrationContext,
    row: Dict[str, Any],
    dest: str,
) -> None:
    source_type = (row.get("source_type") or "").lower()
    source_identifier = (row.get("source_identifier") or "").strip()

    ctx.source_type = ctx.source_type or row.get("source_type")
    if source_identifier:
        ctx.source_connector_id = ctx.source_connector_id or source_identifier

    if dest == "sql" and source_identifier and source_type == "sql":
        ctx.connector_id = ctx.connector_id or source_identifier
    elif dest == "google_sheets" and source_identifier and source_type in (
        "google_sheets",
        "gsheets",
    ):
        ctx.spreadsheet_id = ctx.spreadsheet_id or source_identifier
    elif dest == "salesforce" and source_type == "salesforce" and source_identifier:
        ctx.source_connector_id = ctx.source_connector_id or source_identifier


def _merge_persisted_context(ctx: MigrationContext, db_ctx: MigrationContext) -> None:
    ctx.connector_id = ctx.connector_id or db_ctx.connector_id
    ctx.source_connector_id = ctx.source_connector_id or db_ctx.source_connector_id
    ctx.table_name = ctx.table_name or db_ctx.table_name
    ctx.spreadsheet_id = ctx.spreadsheet_id or db_ctx.spreadsheet_id
    ctx.spreadsheet_name = ctx.spreadsheet_name or db_ctx.spreadsheet_name
    ctx.sheet_name = ctx.sheet_name or db_ctx.sheet_name
    ctx.operation = ctx.operation or db_ctx.operation
    ctx.sql_query = ctx.sql_query or db_ctx.sql_query
    ctx.source_type = ctx.source_type or db_ctx.source_type
    ctx.extracted_schema_type = ctx.extracted_schema_type or db_ctx.extracted_schema_type
    if not ctx.transformation_rules and db_ctx.transformation_rules:
        ctx.transformation_rules = db_ctx.transformation_rules
    if not ctx.migration_rules and db_ctx.migration_rules:
        ctx.migration_rules = db_ctx.migration_rules
    if not ctx.is_transformed_data and db_ctx.is_transformed_data:
        ctx.is_transformed_data = db_ctx.is_transformed_data
    if not ctx.mapping_config_transform_applied and db_ctx.mapping_config_transform_applied:
        ctx.mapping_config_transform_applied = db_ctx.mapping_config_transform_applied
    if not ctx.mapping_id:
        ctx.mapping_id = db_ctx.mapping_id
    if db_ctx.mapping_version:
        ctx.mapping_version = ctx.mapping_version or db_ctx.mapping_version


async def _enrich_sql_destination(ctx: MigrationContext, session: AgentSession) -> None:
    if not ctx.connector_id:
        name = (ctx.destination_name or session.destination_name or "").strip()
        if name and name.lower() != "unknown":
            resolved = await _lookup_sql_connector_id(session.user_id, name)
            if resolved:
                ctx.connector_id = resolved

    if ctx.connector_id and not ctx.source_connector_id:
        ctx.source_connector_id = ctx.connector_id

    if not ctx.table_name:
        ctx.table_name = _infer_sql_table_from_mapping(ctx.mapping)


async def _enrich_gsheets_destination(ctx: MigrationContext, session: AgentSession) -> None:
    if not ctx.spreadsheet_id:
        name = (ctx.destination_name or session.destination_name or "").strip()
        if name and name.lower() != "unknown":
            resolved = await _lookup_gsheets_spreadsheet_id(session.user_id, name)
            if resolved:
                ctx.spreadsheet_id = resolved

    if ctx.spreadsheet_id and not ctx.sheet_name:
        resolved_sheet = await _lookup_gsheets_sheet_name(
            session.user_id, ctx.spreadsheet_id
        )
        if resolved_sheet:
            ctx.sheet_name = resolved_sheet


async def _enrich_salesforce_destination(
    ctx: MigrationContext, session: AgentSession
) -> None:
    if not ctx.connector_id:
        name = (ctx.destination_name or session.destination_name or "").strip()
        if name and name.lower() != "unknown":
            token_id = await _lookup_salesforce_token_id(session.user_id, name)
            if token_id:
                ctx.connector_id = token_id


async def _lookup_sql_connector_id(user_id: str, display_name: str) -> Optional[str]:
    from db.connection import database

    row = await database.fetch_one(
        """
        SELECT id FROM sql_connectors
        WHERE user_id = :user_id
          AND LOWER(TRIM(name)) = LOWER(TRIM(:name))
          AND is_active = true
        LIMIT 1
        """,
        values={"user_id": str(user_id), "name": display_name.strip()},
    )
    if not row:
        return None
    cid = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
    return str(cid) if cid else None


async def _lookup_gsheets_spreadsheet_id(
    user_id: str, display_name: str
) -> Optional[str]:
    from db.connection import database
    from services.migration.db_row import row_get

    row = await database.fetch_one(
        """
        SELECT spreadsheet_id
        FROM gsheets_extracted_schemas
        WHERE user_id = CAST(:user_id AS uuid)
          AND LOWER(TRIM(spreadsheet_name)) = LOWER(TRIM(:spreadsheet_name))
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": str(user_id), "spreadsheet_name": display_name.strip()},
    )
    sid = row_get(row, "spreadsheet_id")
    return str(sid) if sid else None


async def _lookup_gsheets_sheet_name(
    user_id: str, spreadsheet_id: str
) -> Optional[str]:
    from db.connection import database
    from services.migration.db_row import row_get

    row = await database.fetch_one(
        """
        SELECT sheet_name
        FROM gsheets_extracted_schemas
        WHERE user_id = CAST(:user_id AS uuid)
          AND spreadsheet_id = :spreadsheet_id
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": str(user_id), "spreadsheet_id": spreadsheet_id},
    )
    name = row_get(row, "sheet_name")
    return str(name) if name else None


async def _lookup_salesforce_token_id(
    user_id: str, destination_name: str
) -> Optional[str]:
    from db.connection import database

    row = await database.fetch_one(
        """
        SELECT id
        FROM salesforce_tokens
        WHERE user_id = :user_id
          AND LOWER(TRIM(org_display_name)) = LOWER(TRIM(:destination_name))
        ORDER BY is_default DESC NULLS LAST, issued_at DESC
        LIMIT 1
        """,
        values={"user_id": str(user_id), "destination_name": destination_name.strip()},
    )
    if not row:
        return None
    tid = row.get("id") if isinstance(row, dict) else getattr(row, "id", None)
    return str(tid) if tid else None


def _infer_sql_table_from_mapping(mapping: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(mapping, dict) or not mapping:
        return None
    counts: Dict[str, int] = {}
    for spec in mapping.values():
        if not isinstance(spec, dict):
            continue
        if isinstance(spec, list):
            for item in spec:
                if isinstance(item, dict) and item.get("table"):
                    tbl = str(item["table"]).strip()
                    counts[tbl] = counts.get(tbl, 0) + 1
            continue
        tbl = (spec.get("table") or spec.get("table_name") or "").strip()
        if tbl:
            counts[tbl] = counts.get(tbl, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _context_from_session(
    session: AgentSession,
    job_id: str,
    mapping: Dict[str, Any],
    *,
    destination: Any = None,
    destination_name: Optional[str] = None,
) -> MigrationContext:
    dest = _normalize_destination(
        destination if destination is not None else session.destination
    )
    resolved_name = (
        destination_name
        if destination_name is not None
        else session.destination_name
    )
    ctx = MigrationContext(
        user_id=session.user_id,
        extraction_job_id=job_id,
        mapping=mapping,
        document_type=_doc_type_from_session(session),
        destination=dest,
        destination_name=resolved_name,
        conversation_id=session.conversation_id,
        mapping_id=session.mapping_id,
        mapping_version=session.mapping_version or 1,
    )
    _apply_session_hints(ctx, _session_hints(session), dest)
    return ctx


def _enrich_context_from_meta(ctx: MigrationContext, meta: Dict[str, Any]) -> None:
    from utils.migration_rules_utils import get_mapping_transformation

    if meta.get("destination"):
        ctx.destination = _normalize_destination(meta.get("destination"))
    if meta.get("destination_name"):
        ctx.destination_name = str(meta.get("destination_name"))
    mt = get_mapping_transformation(meta)
    if mt and isinstance(mt.get("fields"), list) and mt["fields"]:
        ctx.transformation_rules = mt
    if meta.get("connector_id"):
        ctx.connector_id = str(meta["connector_id"])
    if meta.get("destination_connector_id"):
        ctx.connector_id = str(meta["destination_connector_id"])
    if meta.get("table_name") or meta.get("primary_table"):
        ctx.table_name = meta.get("table_name") or meta.get("primary_table")
    if meta.get("spreadsheet_id") or meta.get("destination_spreadsheet_id"):
        ctx.spreadsheet_id = str(
            meta.get("spreadsheet_id") or meta.get("destination_spreadsheet_id")
        )
    if meta.get("sheet_name") or meta.get("destination_sheet_name"):
        ctx.sheet_name = meta.get("sheet_name") or meta.get("destination_sheet_name")
    if meta.get("spreadsheet_name"):
        ctx.spreadsheet_name = str(meta["spreadsheet_name"])
    if meta.get("operation"):
        ctx.operation = str(meta["operation"])
    if meta.get("source_connector_id"):
        ctx.source_connector_id = str(meta["source_connector_id"])
    if meta.get("sql_query"):
        ctx.sql_query = meta["sql_query"]
    if meta.get("source_type"):
        ctx.source_type = meta["source_type"]
    if meta.get("extracted_schema_type"):
        ctx.extracted_schema_type = meta["extracted_schema_type"]


def _doc_type_from_session(session: AgentSession) -> str:
    snap = session.classification_snapshot or {}
    last = session.last_result or {}
    contract = snap.get("plan_contract") or {}
    dest = contract.get("destination") if isinstance(contract, dict) else {}
    if isinstance(dest, dict):
        pinned = (dest.get("entity_key") or "").strip()
        if pinned:
            if "::" in pinned:
                return pinned.split("::", 1)[-1].strip() or pinned
            return pinned
    return (
        last.get("destination_document_type")
        or last.get("document_type")
        or snap.get("destination_object_api_name")
        or snap.get("destination_table_name")
        or snap.get("destination_sheet_name")
        or ""
    )
