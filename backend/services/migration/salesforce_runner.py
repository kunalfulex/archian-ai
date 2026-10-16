"""Start Salesforce background migration (shared by HTTP + agent)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from db.connection import database
from services.background_migration_service import background_migration_service
from services.chat_service import ChatService
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
from utils.salesforceapimigration import get_valid_salesforce_token


async def _resolve_destination_token(
    user_id: str,
    destination_name: Optional[str],
    *,
    token_id: Optional[str] = None,
) -> Dict[str, Any]:
    from utils.destination_connector_resolve import resolve_salesforce_token_record

    token_record = await resolve_salesforce_token_record(
        user_id,
        destination_name=destination_name,
        token_id=token_id,
    )
    if not token_record and not (
        destination_name and destination_name.strip() and destination_name != "unknown"
    ):
        token_record = await resolve_salesforce_token_record(user_id)

    if not token_record:
        if destination_name and destination_name.strip() and destination_name != "unknown":
            raise MigrationServiceError(
                f"Salesforce connection '{destination_name}' not found.",
                status_code=404,
            )
        raise MigrationServiceError(
            "No Salesforce connections found for this user. Please connect a Salesforce org.",
            status_code=404,
        )
    return dict(token_record)


async def _resolve_source_salesforce_credentials(
    *,
    user_id: str,
    source_job_id: str,
    connector_id: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    source_access_token = None
    source_instance_url = None

    if connector_id:
        try:
            from uuid import UUID

            source_token_row = None
            try:
                UUID(str(connector_id))
                source_token_row = await database.fetch_one(
                    """
                    SELECT org_id, connected_app_id
                    FROM salesforce_tokens
                    WHERE user_id = :user_id AND id = :token_id
                    LIMIT 1
                    """,
                    values={"user_id": user_id, "token_id": str(connector_id)},
                )
            except (ValueError, TypeError):
                pass

            if not source_token_row and str(connector_id).isdigit():
                source_token_row = await database.fetch_one(
                    """
                    SELECT org_id, connected_app_id
                    FROM salesforce_tokens
                    WHERE user_id = :user_id AND connected_app_id = :connected_app_id
                    ORDER BY issued_at DESC
                    LIMIT 1
                    """,
                    values={
                        "user_id": user_id,
                        "connected_app_id": int(connector_id),
                    },
                )

            if source_token_row:
                src_row = dict(source_token_row)
                source_token_info = await get_valid_salesforce_token(
                    user_id=user_id,
                    org_id=src_row["org_id"],
                    connected_app_id=src_row.get("connected_app_id"),
                )
                source_access_token = source_token_info["access_token"]
                source_instance_url = source_token_info["instance_url"]
        except Exception:
            pass

    if not source_access_token and source_job_id:
        try:
            from utils.job_stores import extraction_jobs

            job_data = await extraction_jobs.get(source_job_id, {}) or {}
            if job_data and job_data.get("source_type") == "salesforce":
                source_org_id = job_data.get("org_id")
                source_connected_app_id = job_data.get("connected_app_id")
                if source_org_id or source_connected_app_id:
                    source_token_info = await get_valid_salesforce_token(
                        user_id=user_id,
                        org_id=source_org_id,
                        connected_app_id=source_connected_app_id,
                    )
                    source_access_token = source_token_info["access_token"]
                    source_instance_url = source_token_info["instance_url"]
            else:
                extraction_result = await ExtractionResultsService.get_extraction_result(
                    source_job_id, user_id
                )
                if extraction_result and extraction_result.get("source_type") == "salesforce":
                    source_identifier = extraction_result.get("source_identifier")
                    if source_identifier:
                        source_token_info = await get_valid_salesforce_token(
                            user_id=user_id,
                            org_id=source_identifier,
                        )
                        source_access_token = source_token_info["access_token"]
                        source_instance_url = source_token_info["instance_url"]
        except Exception:
            pass

    return source_access_token, source_instance_url


async def start_salesforce_migration(ctx: MigrationContext) -> MigrationStartResult:
    user_id = ctx.user_id
    source_job_id = ctx.extraction_job_id
    mapping = dict(ctx.mapping)
    document_type = ctx.document_type

    if not source_job_id:
        raise MigrationServiceError(
            "source_job_id (or job_id) is required. Migration loads records from the database by extraction job_id.",
            status_code=400,
        )
    if not mapping or not document_type:
        raise MigrationServiceError("Missing mapping or document type.", status_code=400)

    total_records = await validate_extraction_for_migration(
        user_id,
        source_job_id,
        record_ordinals=ctx.record_ordinals,
    )

    extraction_row = await ExtractionResultsService.get_extraction_result(
        source_job_id, user_id
    )
    if extraction_row:
        data = extraction_row.get("extracted_data") or []
        sample_keys = []
        if isinstance(data, list) and data and isinstance(data[0], dict):
            sample_keys = sorted(list(data[0].keys()))[:12]
        print(
            f"📦 [MIGRATE-SF] source_job_id={source_job_id} "
            f"source_type={extraction_row.get('source_type')} "
            f"source_name={extraction_row.get('source_name')} "
            f"records={total_records} sample_keys={sample_keys}"
        )

    await validate_plan_for_migration(user_id, total_records)

    destination_name = ctx.destination_name
    token_record_dict = await _resolve_destination_token(user_id, destination_name)
    org_id = token_record_dict["org_id"]
    salesforce_token_id = (
        str(token_record_dict["id"]) if token_record_dict.get("id") else None
    )
    connected_app_id = token_record_dict.get("connected_app_id")
    if connected_app_id is not None:
        connected_app_id = int(connected_app_id)
    if not destination_name or destination_name == "unknown":
        destination_name = (
            token_record_dict.get("org_display_name") or org_id or "Salesforce"
        )

    if not org_id:
        raise MigrationServiceError(
            "Salesforce connection is not authenticated. Please connect the org and try again.",
            status_code=400,
        )

    token_info = await get_valid_salesforce_token(
        user_id=user_id,
        org_id=org_id,
        connected_app_id=connected_app_id,
    )
    access_token = token_info["access_token"]
    instance_url = token_info["instance_url"]

    connector_id = ctx.connector_id or ctx.source_connector_id
    source_access_token, source_instance_url = await _resolve_source_salesforce_credentials(
        user_id=user_id,
        source_job_id=source_job_id,
        connector_id=connector_id,
    )

    is_transformed_data = ctx.is_transformed_data
    transformation_rules = ctx.transformation_rules
    migration_rules = ctx.migration_rules or {}

    # Mapping transform: agent path applies rules per batch in background_migration_service
    # (same as SQL/GSheets). Persisted child jobs only when user clicks preview (/transform-data).

    if extraction_row and mapping and not is_transformed_data:
        from utils.mapping_utils import align_mapping_keys_to_extracted_data

        data = extraction_row.get("extracted_data") or []
        sample_row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
        mapping = align_mapping_keys_to_extracted_data(mapping, sample_row)
    mapping = transform_mapping_for_destination(
        mapping,
        is_transformed_data=is_transformed_data,
        transformation_rules=transformation_rules,
        destination="salesforce",
    )
    from utils.sf_mapping_lookup_sanitize import strip_empty_lookup_of_from_mapping

    mapping = strip_empty_lookup_of_from_mapping(mapping)
    await check_migration_credits(user_id, total_records)

    batch_name = f"migration_{document_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
    from utils.rollback_mapping_keys import build_mapping_config_for_batch

    batch_mapping_config = build_mapping_config_for_batch(
        mapping=mapping,
        destination="salesforce",
        base={
            "document_type": document_type,
            "field_count": len(mapping),
            "field_names": list(mapping.keys()),
            "objects_involved": list(
                {
                    m.get("object", "Unknown")
                    for m in mapping.values()
                    if isinstance(m, dict)
                }
            ),
        },
    )
    if destination_name:
        batch_mapping_config["destination_name"] = destination_name
    if org_id:
        batch_mapping_config["org_id"] = org_id
    if connected_app_id is not None:
        batch_mapping_config["connected_app_id"] = connected_app_id

    batch_id = await MigrationRecordsService.create_migration_batch(
        user_id=user_id,
        batch_name=batch_name,
        document_type=document_type,
        total_records=total_records,
        mapping_config=batch_mapping_config,
        destination_type="salesforce",
    )

    from utils.batch_config import compute_migration_batch_size

    # Keep split-field arrays intact for batch transform (same as SQL/GSheets). Flattening for
    # Bulk API happens in background_migration_service after transform_data_for_migration.
    # Preview-only jobs (is_transformed_data) already use destination keys on rows — flatten now.
    migration_mapping = mapping
    if is_transformed_data:
        from utils.mapping_utils import transform_mapping_for_migration

        migration_mapping = transform_mapping_for_migration(
            mapping,
            use_destination_field_keys=True,
        )

    migration_rules = dict(ctx.migration_rules or {})
    if ctx.record_ordinals:
        migration_rules["record_ordinals"] = list(ctx.record_ordinals)
    if ctx.parent_batch_id:
        migration_rules["parent_batch_id"] = ctx.parent_batch_id

    job_id = await background_migration_service.start_background_migration(
        user_id=user_id,
        mapping=migration_mapping,
        document_type=document_type,
        access_token=access_token,
        instance_url=instance_url,
        batch_id=batch_id,
        batch_size=compute_migration_batch_size(total_records, "salesforce"),
        max_concurrent=5,
        org_id=org_id,
        connected_app_id=connected_app_id,
        destination_name=destination_name,
        salesforce_token_id=salesforce_token_id,
        source_access_token=source_access_token,
        source_instance_url=source_instance_url,
        source_job_id=source_job_id,
        total_records=total_records,
        transformation_rules=transformation_rules,
        is_transformed_data=is_transformed_data,
        conversation_id=ctx.conversation_id,
        migration_rules=migration_rules or ctx.migration_rules,
    )

    if ctx.conversation_id:
        try:
            await ChatService.log_data_event(
                ctx.conversation_id,
                user_id,
                "migration_started",
                {
                    "job_id": job_id,
                    "batch_id": batch_id,
                    "source_job_id": source_job_id,
                    "destination": "salesforce",
                    "destination_name": destination_name,
                    "destination_connector_id": salesforce_token_id,
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
        destination="salesforce",
        destination_name=destination_name,
        source_job_id=source_job_id,
        instance_url=instance_url,
        org_id=org_id,
        connected_app_id=connected_app_id,
        extra={
            "source_job_id": source_job_id,
            "destination_connector_id": str(salesforce_token_id) if salesforce_token_id else None,
            "destination": "salesforce",
            "destination_name": destination_name,
        },
    )
