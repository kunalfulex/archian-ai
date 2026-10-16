"""Shared validation and billing checks for all migration destinations."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.extraction_results_service import ExtractionResultsService
from services.migration.errors import MigrationServiceError
from services.entitlements import get_entitlements


async def validate_extraction_for_migration(
    user_id: str,
    source_job_id: str,
    *,
    record_ordinals: Optional[list] = None,
) -> int:
    if record_ordinals is not None:
        ordinals = sorted({int(o) for o in record_ordinals})
        if not ordinals:
            raise MigrationServiceError(
                "No recoverable records to migrate.",
                status_code=400,
            )
        exists = await ExtractionResultsService.get_extraction_record_count(
            source_job_id, user_id
        )
        if exists is None:
            raise MigrationServiceError(
                f"Extraction result not found for job_id {source_job_id}. "
                "Please ensure the extraction completed successfully.",
                status_code=404,
            )
        if await ExtractionResultsService.is_extraction_detail_expired(
            source_job_id, user_id
        ):
            raise MigrationServiceError(
                ExtractionResultsService.MIGRATION_DETAIL_EXPIRED_MESSAGE,
                status_code=400,
            )
        return len(ordinals)

    total_records = await ExtractionResultsService.get_extraction_record_count(
        source_job_id, user_id
    )
    if total_records is None:
        raise MigrationServiceError(
            f"Extraction result not found for job_id {source_job_id}. "
            "Please ensure the extraction completed successfully.",
            status_code=404,
        )
    if await ExtractionResultsService.is_extraction_detail_expired(
        source_job_id, user_id
    ):
        raise MigrationServiceError(
            ExtractionResultsService.MIGRATION_DETAIL_EXPIRED_MESSAGE,
            status_code=400,
        )
    if total_records == 0:
        raise MigrationServiceError(
            "No records to migrate for this extraction.",
            status_code=400,
        )
    return total_records


async def validate_plan_for_migration(
    user_id: str, total_records: int
) -> None:
    is_valid, message, plan_info = await get_entitlements().validate_migration(
        user_id, total_records
    )
    if not is_valid:
        raise MigrationServiceError(
            message,
            status_code=403,
            payload={
                "status": "error",
                "message": message,
                "plan_info": plan_info,
                "total_records": total_records,
            },
        )


async def check_migration_credits(user_id: str, total_records: int) -> None:
    user_plan = await get_entitlements().get_user_plan(user_id)
    credits_per_month = user_plan.get("credits_per_month") if user_plan else None
    if credits_per_month == -1:
        return
    from utils.credit_config import compute_migration_credits

    estimated_credits = compute_migration_credits(total_records)
    if estimated_credits > 0 and not await get_entitlements().has_sufficient_balance(
        user_id, estimated_credits
    ):
        raise MigrationServiceError(
            "Insufficient credits to run this migration. Buy more credits or upgrade your plan.",
            status_code=402,
        )


def transform_mapping_for_destination(
    mapping: Dict[str, Any],
    *,
    is_transformed_data: bool,
    transformation_rules: Optional[Dict[str, Any]],
    destination: str,
) -> Dict[str, Any]:
    if not (
        is_transformed_data
        and transformation_rules
        and transformation_rules.get("fields")
    ):
        return mapping
    try:
        from services.data_transformation_service import DataTransformationService

        return DataTransformationService.transform_mapping_for_transformed_data(
            mapping=mapping,
            transformation_rules=transformation_rules,
            destination=destination,
        )
    except Exception as e:
        print(f"⚠️ Error transforming mapping for {destination}: {e}")
        return mapping
