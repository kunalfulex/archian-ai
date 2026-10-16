"""Start SQL database background migration (shared by HTTP + agent)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from services.chat_service import ChatService
from services.database_background_migration_service import database_background_migration_service
from services.extraction_results_service import ExtractionResultsService
from services.migration.common import (
    check_migration_credits,
    transform_mapping_for_destination,
    validate_extraction_for_migration,
    validate_plan_for_migration,
)
from services.migration.context import MigrationContext, MigrationStartResult
from services.migration.errors import MigrationServiceError
from services.migration_records_service import MigrationRecordsService
from utils.batch_config import compute_migration_batch_size
from utils.sql_mapping_sanitize import debug_log_sql_mapping, sanitize_sql_mapping_for_migration


async def _resolve_sql_connector_and_table(
    ctx: MigrationContext,
    mapping: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Last-line resolver when session enrichment did not set IDs (agent same-turn migrate)."""
    from services.migration.session_migration import (
        _infer_sql_table_from_mapping,
        _lookup_sql_connector_id,
    )

    connector_id = ctx.connector_id
    table_name = ctx.table_name
    if not connector_id:
        row = await ExtractionResultsService.get_extraction_result(
            ctx.extraction_job_id, ctx.user_id
        )
        if row and (row.get("source_type") or "").lower() == "sql":
            sid = (row.get("source_identifier") or "").strip()
            if sid:
                connector_id = sid
                ctx.source_connector_id = ctx.source_connector_id or sid
    if not connector_id and ctx.destination_name:
        name = ctx.destination_name.strip()
        if name and name.lower() != "unknown":
            connector_id = await _lookup_sql_connector_id(ctx.user_id, name)
    if not table_name:
        table_name = _infer_sql_table_from_mapping(mapping)
    if connector_id:
        ctx.connector_id = connector_id
    if table_name:
        ctx.table_name = table_name
    return connector_id, table_name


async def start_sql_migration(ctx: MigrationContext) -> MigrationStartResult:
    user_id = ctx.user_id
    source_job_id = ctx.extraction_job_id
    mapping = dict(ctx.mapping or {})
    connector_id, table_name = await _resolve_sql_connector_and_table(ctx, mapping)

    if not source_job_id:
        raise MigrationServiceError(
            "source_job_id (or job_id) is required. Migration loads records from the database by extraction job_id.",
            status_code=400,
        )
    if not mapping:
        raise MigrationServiceError("No mapping provided.", status_code=400)
    if not connector_id:
        raise MigrationServiceError(
            "Database connector ID is required.", status_code=400
        )
    if not table_name:
        raise MigrationServiceError(
            "Destination table name is required.", status_code=400
        )

    total_records = await validate_extraction_for_migration(user_id, source_job_id)
    await validate_plan_for_migration(user_id, total_records)
    await check_migration_credits(user_id, total_records)

    raw_mapping = mapping
    tables_structure = None
    if isinstance(ctx.migration_rules, dict):
        raw_tables = ctx.migration_rules.get("available_tables")
        if isinstance(raw_tables, dict):
            tables_structure = raw_tables
    mapping = sanitize_sql_mapping_for_migration(
        mapping,
        table_name,
        tables_structure=tables_structure,
    )
    dropped_lookup: List[str] = []
    if raw_mapping != mapping:
        for field_key, config in (raw_mapping or {}).items():
            items = config if isinstance(config, list) else [config]
            iter_items = items if isinstance(items, list) else [items]
            for idx, item in enumerate(iter_items):
                if not isinstance(item, dict):
                    continue
                lo = (item.get("lookup_of") or "").strip()
                if not lo:
                    continue
                sanitized_cfg = mapping.get(field_key)
                if isinstance(sanitized_cfg, list):
                    san = sanitized_cfg[idx] if idx < len(sanitized_cfg) else {}
                else:
                    san = sanitized_cfg if isinstance(sanitized_cfg, dict) else {}
                if not (san.get("lookup_of") or "").strip():
                    dropped_lookup.append(f"{field_key}: {lo}")
    debug_log_sql_mapping(mapping, table_name, dropped_reasons=dropped_lookup)

    is_transformed_data = ctx.is_transformed_data
    transformation_rules = ctx.transformation_rules
    migration_rules = ctx.migration_rules or {}
    mapping = transform_mapping_for_destination(
        mapping,
        is_transformed_data=is_transformed_data,
        transformation_rules=transformation_rules,
        destination="sql",
    )

    document_type = (
        ctx.document_type
        or ctx.document_description
        or "Database Migration"
    )
    destination_name = ctx.destination_name or table_name
    from services.migration.write_policy import resolve_write_policy

    write_policy = resolve_write_policy(
        destination="sql",
        mapping=mapping,
        explicit_operation=ctx.operation,
    )
    operation = write_policy.operation

    batch_name = (
        f"migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    )
    from utils.rollback_mapping_keys import build_mapping_config_for_batch

    mapping_config = build_mapping_config_for_batch(
        mapping=mapping,
        destination="sql",
        base={
            "document_type": document_type,
            "connector_id": connector_id,
            "table_name": table_name,
            "destination_name": destination_name,
            "operation": operation,
            "field_count": len(mapping),
            "field_names": list(mapping.keys()),
        },
    )
    batch_id = await MigrationRecordsService.create_migration_batch(
        user_id=user_id,
        batch_name=batch_name,
        document_type=document_type,
        total_records=total_records,
        mapping_config=mapping_config,
        destination_type="sql",
    )

    background_job_id = await database_background_migration_service.start_background_migration(
        user_id=user_id,
        mapping=mapping,
        connector_id=connector_id,
        table_name=table_name,
        document_type=document_type,
        operation=operation,
        batch_id=batch_id,
        destination_name=destination_name,
        batch_size=compute_migration_batch_size(total_records, "sql"),
        source_job_id=source_job_id,
        total_records=total_records,
        transformation_rules=transformation_rules,
        is_transformed_data=is_transformed_data,
        migration_rules=migration_rules,
        conversation_id=ctx.conversation_id,
    )

    if ctx.conversation_id and batch_id:
        try:
            src_type = (ctx.source_type or ctx.extracted_schema_type or "sql").lower()
            ext_schema = (ctx.extracted_schema_type or ctx.source_type or "sql").lower()
            if ext_schema == "google_sheets":
                ext_schema = "gsheets"
            if src_type == "google_sheets":
                src_type = "gsheets"
            if not ctx.sql_query:
                try:
                    extraction = await ExtractionResultsService.get_extraction_result(
                        source_job_id, user_id
                    )
                    if extraction:
                        st = extraction.get("source_type") or "sql"
                        src_type = src_type or st
                        ext_schema = ext_schema or st
                except Exception:
                    pass
            migration_started_meta = {
                "job_id": background_job_id,
                "batch_id": batch_id,
                "source_job_id": source_job_id,
                "destination": "sql",
                "destination_name": destination_name,
                "destination_connector_id": connector_id,
                "record_count": total_records,
                "total_records": total_records,
                "source_type": src_type,
                "extracted_schema_type": ext_schema,
                "sql_query": ctx.sql_query,
                "table_name": table_name,
                "connector_id": ctx.source_connector_id,
                "source_connector_id": ctx.source_connector_id,
                "document_type": ctx.document_type,
                "document_description": ctx.document_description,
            }
            await ChatService.log_data_event(
                conversation_id=ctx.conversation_id,
                user_id=user_id,
                event_type="migration_started",
                event_data={
                    k: v
                    for k, v in migration_started_meta.items()
                    if v is not None
                },
            )
        except Exception as e:
            print(f"⚠️ Failed to log migration_started event: {e}")

    return MigrationStartResult(
        job_id=background_job_id,
        batch_id=batch_id,
        total_records=total_records,
        destination="sql",
        destination_name=destination_name,
        source_job_id=source_job_id,
        extra={
            "operation": operation,
            "connector_id": connector_id,
            "destination_connector_id": str(connector_id) if connector_id else None,
            "table_name": table_name,
            "destination": "sql",
            "source_type": ctx.source_type or "sql",
            "extracted_schema_type": ctx.extracted_schema_type
            or ctx.source_type
            or "sql",
            "sql_query": ctx.sql_query,
            "source_connector_id": ctx.source_connector_id,
        },
    )
