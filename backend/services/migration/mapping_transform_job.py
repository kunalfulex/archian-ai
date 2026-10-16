"""Persist mapping-stage transforms as a child extraction job (preview /transform-data only)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class MappingTransformJobResult:
    job_id: str
    mapping: Dict[str, Any]
    is_transformed_data: bool
    created: bool
    record_count: Optional[int] = None
    transformation_metadata: Optional[Dict[str, Any]] = None
    transformed_data: Optional[list] = None


def _has_mapping_transform_rules(transformation_rules: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(transformation_rules, dict):
        return False
    fields = transformation_rules.get("fields")
    return isinstance(fields, list) and len(fields) > 0


def is_mapping_transform_job(row: Optional[Dict[str, Any]]) -> bool:
    """
    True when this extraction job's row data already went through mapping-stage transform.

    Distinct from ``has_transformation_rules``, which marks post-extract / extraction_rules
    transforms only.
    """
    if not row:
        return False
    if row.get("has_mapping_transform"):
        return True
    meta = row.get("extraction_metadata")
    if isinstance(meta, dict) and meta.get("mapping_transform_applied"):
        return True
    return False


async def ensure_mapping_transform_job(
    *,
    user_id: str,
    source_job_id: str,
    mapping: Dict[str, Any],
    transformation_rules: Optional[Dict[str, Any]],
    destination: str,
    org_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    extraction_result: Optional[Dict[str, Any]] = None,
) -> MappingTransformJobResult:
    """
    Run mapping-stage transform and save a child extraction job when rules exist.

    Used by the preview transformation API only. Agent migrate paths (Salesforce, SQL,
    Google Sheets) apply mapping rules per batch at send time without persisting a job.

    Idempotent when ``source_job_id`` already points at a preview mapping-transform job
    (``has_mapping_transform`` on the extraction row).
    """
    if not _has_mapping_transform_rules(transformation_rules):
        return MappingTransformJobResult(
            job_id=source_job_id,
            mapping=dict(mapping or {}),
            is_transformed_data=False,
            created=False,
        )

    from services.data_transformation_service import DataTransformationService
    from services.extraction_results_service import ExtractionResultsService

    row = extraction_result
    if row is None:
        row = await ExtractionResultsService.get_extraction_result(source_job_id, user_id)
    if not row:
        raise ValueError(f"Extraction result not found for job_id: {source_job_id}")

    if is_mapping_transform_job(row):
        transformed_mapping = DataTransformationService.transform_mapping_for_transformed_data(
            mapping=dict(mapping or {}),
            transformation_rules=transformation_rules or {"fields": []},
            destination=destination,
        )
        print(
            f"🔧 [MIGRATE_TRANSFORM] Reusing mapping-transformed job "
            f"source_job_id={source_job_id}"
        )
        return MappingTransformJobResult(
            job_id=source_job_id,
            mapping=transformed_mapping,
            is_transformed_data=True,
            created=False,
            record_count=int(row.get("record_count") or 0) or None,
            transformed_data=row.get("extracted_data"),
        )

    extracted_data = row.get("extracted_data") or []
    if not extracted_data:
        raise ValueError(f"No extraction records for job_id: {source_job_id}")

    transformed_data, transformation_metadata, transformed_mapping = (
        await DataTransformationService.transform_data_for_migration(
            data=extracted_data,
            mapping=dict(mapping or {}),
            transformation_rules=transformation_rules or {"fields": []},
            user_id=user_id,
            org_id=org_id,
            destination=destination,
        )
    )

    transformed_job_id = str(uuid.uuid4())
    parent_meta = row.get("extraction_metadata")
    parent_meta = dict(parent_meta) if isinstance(parent_meta, dict) else {}
    mapping_meta = {
        **parent_meta,
        "mapping_transform_applied": True,
        "mapping_transform_parent_job_id": source_job_id,
    }
    await ExtractionResultsService.save_extraction_result(
        job_id=transformed_job_id,
        user_id=user_id,
        extracted_data=transformed_data,
        source_type=row.get("source_type", "file"),
        extraction_goals=row.get("extraction_goals"),
        document_type=row.get("document_type"),
        document_description=row.get("document_description"),
        source_name=row.get("source_name"),
        source_identifier=row.get("source_identifier"),
        parent_job_id=source_job_id,
        conversation_id=conversation_id or row.get("conversation_id"),
        has_transformation_rules=bool(row.get("has_transformation_rules")),
        has_mapping_transform=True,
        extraction_metadata=mapping_meta,
    )

    print(
        f"🔧 [MIGRATE_TRANSFORM] Saved mapping-transform child job "
        f"parent={source_job_id} child={transformed_job_id} "
        f"records={len(transformed_data)} destination={destination}"
    )

    return MappingTransformJobResult(
        job_id=transformed_job_id,
        mapping=transformed_mapping,
        is_transformed_data=True,
        created=True,
        record_count=len(transformed_data),
        transformation_metadata=transformation_metadata,
        transformed_data=transformed_data,
    )
