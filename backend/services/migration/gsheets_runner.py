"""Start Google Sheets background migration (shared by HTTP + agent)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from db.connection import database
from services.migration.db_row import row_get
from services.chat_service import ChatService
from services.gsheets_background_migration_service import gsheets_background_migration_service
from services.google_sheets_connection_service import connection_id_for_spreadsheet
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


async def _resolve_spreadsheet_id(
    user_id: str,
    spreadsheet_id: Optional[str],
    destination_name: Optional[str],
) -> str:
    if spreadsheet_id and str(spreadsheet_id).strip():
        return str(spreadsheet_id).strip()
    if not destination_name or destination_name.strip().lower() == "unknown":
        raise MigrationServiceError(
            "spreadsheet_id is required for Google Sheets migration.",
            status_code=400,
        )
    row = await database.fetch_one(
        """
        SELECT spreadsheet_id
        FROM gsheets_extracted_schemas
        WHERE user_id = CAST(:user_id AS uuid)
          AND LOWER(TRIM(spreadsheet_name)) = LOWER(TRIM(:spreadsheet_name))
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": user_id, "spreadsheet_name": destination_name.strip()},
    )
    sid = row_get(row, "spreadsheet_id")
    if sid:
        return str(sid)
    raise MigrationServiceError(
        f"Could not resolve spreadsheet for '{destination_name}'.",
        status_code=400,
    )


async def _resolve_sheet_name(
    user_id: str,
    spreadsheet_id: str,
    sheet_name: Optional[str],
) -> str:
    if sheet_name and str(sheet_name).strip():
        return str(sheet_name).strip()
    row = await database.fetch_one(
        """
        SELECT sheet_name
        FROM gsheets_extracted_schemas
        WHERE user_id = CAST(:user_id AS uuid)
          AND spreadsheet_id = :spreadsheet_id
        ORDER BY extracted_at DESC NULLS LAST
        LIMIT 1
        """,
        {"user_id": user_id, "spreadsheet_id": spreadsheet_id},
    )
    name = row_get(row, "sheet_name")
    if name:
        return str(name)
    raise MigrationServiceError(
        "sheet_name is required for Google Sheets migration.",
        status_code=400,
    )


async def start_google_sheets_migration(ctx: MigrationContext) -> MigrationStartResult:
    user_id = ctx.user_id
    source_job_id = ctx.extraction_job_id
    mapping = dict(ctx.mapping or {})
    if not source_job_id:
        raise MigrationServiceError(
            "source_job_id (or job_id) is required. Migration loads records from the database by extraction job_id.",
            status_code=400,
        )
    if not mapping:
        raise MigrationServiceError("No mapping provided.", status_code=400)

    total_records = await validate_extraction_for_migration(user_id, source_job_id)
    await validate_plan_for_migration(user_id, total_records)
    await check_migration_credits(user_id, total_records)

    spreadsheet_id = await _resolve_spreadsheet_id(
        user_id, ctx.spreadsheet_id, ctx.destination_name
    )
    sheet_name = await _resolve_sheet_name(
        user_id,
        spreadsheet_id,
        ctx.sheet_name,
    )
    gs_connection_id = await connection_id_for_spreadsheet(user_id, spreadsheet_id)
    spreadsheet_name = (
        ctx.spreadsheet_name
        or ctx.destination_name
        or sheet_name
        or "Google Sheets"
    )
    document_type = ctx.document_type or "Google Sheets Migration"

    is_transformed_data = ctx.is_transformed_data
    transformation_rules = ctx.transformation_rules
    migration_rules = ctx.migration_rules or {}

    from services.migration.transform_state import should_apply_mapping_config_transform
    from services.migration.write_policy import resolve_write_policy

    write_policy = resolve_write_policy(
        destination="google_sheets",
        mapping=mapping,
        explicit_operation=ctx.operation,
    )
    operation = write_policy.operation

    if should_apply_mapping_config_transform(
        is_transformed_data=is_transformed_data,
        mapping_config_transform_applied=ctx.mapping_config_transform_applied,
        transformation_rules=transformation_rules,
    ):
        from services.planning.flow_steps import apply_mapping_transformation_to_config

        mapping = apply_mapping_transformation_to_config(
            mapping,
            transformation_rules,
            destination="google_sheets",
        )

    mapping = transform_mapping_for_destination(
        mapping,
        is_transformed_data=is_transformed_data,
        transformation_rules=transformation_rules,
        destination="google_sheets",
    )

    batch_name = (
        f"migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    )
    from utils.rollback_mapping_keys import build_mapping_config_for_batch

    mapping_config = build_mapping_config_for_batch(
        mapping=mapping,
        destination="google_sheets",
        base={
            "document_type": document_type,
            "spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "spreadsheet_name": spreadsheet_name,
            "operation": operation,
            "field_count": len(mapping),
            "field_names": list(mapping.keys()),
            "google_sheets_connection_id": gs_connection_id,
        },
    )
    batch_id = await MigrationRecordsService.create_migration_batch(
        user_id=user_id,
        batch_name=batch_name,
        document_type=document_type,
        total_records=total_records,
        mapping_config=mapping_config,
        destination_type="google_sheets",
    )

    job_id = await gsheets_background_migration_service.start_background_migration(
        user_id=user_id,
        mapping=mapping,
        document_type=document_type,
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        spreadsheet_name=spreadsheet_name,
        operation=operation,
        batch_id=batch_id,
        batch_size=compute_migration_batch_size(total_records, "google_sheets"),
        source_job_id=source_job_id,
        total_records=total_records,
        transformation_rules=transformation_rules,
        is_transformed_data=is_transformed_data,
        migration_rules=migration_rules,
        conversation_id=ctx.conversation_id,
        google_sheets_connection_id=gs_connection_id,
    )

    if ctx.conversation_id and batch_id:
        try:
            await ChatService.log_data_event(
                conversation_id=ctx.conversation_id,
                user_id=user_id,
                event_type="migration_started",
                event_data={
                    "job_id": job_id,
                    "batch_id": batch_id,
                    "source_job_id": source_job_id,
                    "destination": "google_sheets",
                    "destination_name": spreadsheet_name,
                    "destination_connector_id": spreadsheet_id,
                    "destination_spreadsheet_id": spreadsheet_id,
                    "spreadsheet_id": spreadsheet_id,
                    "record_count": total_records,
                    "total_records": total_records,
                },
            )
        except Exception as e:
            print(f"⚠️ Failed to log migration_started event: {e}")

    return MigrationStartResult(
        job_id=job_id,
        batch_id=batch_id,
        total_records=total_records,
        destination="google_sheets",
        destination_name=spreadsheet_name,
        source_job_id=source_job_id,
        extra={
            "spreadsheet_url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "spreadsheet_id": spreadsheet_id,
            "destination_connector_id": spreadsheet_id,
            "destination_spreadsheet_id": spreadsheet_id,
            "sheet_name": sheet_name,
            "operation": operation,
            "destination": "google_sheets",
        },
    )
