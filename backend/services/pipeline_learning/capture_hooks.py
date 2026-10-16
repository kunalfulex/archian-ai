"""Thin hooks for pipeline learning capture (call from handlers without heavy imports)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.pipeline_learning.service import PipelineLearningService
from utils.extraction_rule_pipeline import extraction_rules_from_ai_record
from utils.learning_diff import copy_rules_snapshot


def schedule_extraction_capture(
    *,
    user_id: str,
    conversation_id: Optional[str],
    job_id: str,
    ai_rules: Optional[Dict[str, Any]],
    final_rules: Optional[Dict[str, Any]],
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    source_schema: Optional[List[str]] = None,
    stage_version: int = 1,
    prompt: str = "",
    mapping_context_summary: Optional[str] = None,
) -> None:
    PipelineLearningService.schedule_capture(
        lambda: PipelineLearningService.capture_extraction(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
            ai_rules=ai_rules,
            final_rules=final_rules,
            source_type=source_type,
            destination_type=destination_type,
            source_schema=source_schema,
            stage_version=stage_version,
            prompt=prompt,
            mapping_context_summary=mapping_context_summary,
        )
    )


def schedule_extraction_from_records(
    *,
    user_id: str,
    conversation_id: Optional[str],
    job_id: str,
    extracted_data: List[Dict[str, Any]],
    real_data: List[Dict[str, Any]],
    source_type: Optional[str] = None,
    destination_type: Optional[str] = None,
    stage_version: int = 1,
    prompt: str = "",
    mapping_context_summary: Optional[str] = None,
) -> None:
    if not extracted_data:
        return
    meta = extracted_data[0] if isinstance(extracted_data[0], dict) else {}
    ai_rules = copy_rules_snapshot(extraction_rules_from_ai_record(meta))
    final_meta = real_data[0] if real_data and isinstance(real_data[0], dict) else meta
    final_rules = copy_rules_snapshot(extraction_rules_from_ai_record(final_meta))
    schema = list(real_data[0].keys()) if real_data and isinstance(real_data[0], dict) else None
    schedule_extraction_capture(
        user_id=user_id,
        conversation_id=conversation_id,
        job_id=job_id,
        ai_rules=ai_rules,
        final_rules=final_rules,
        source_type=source_type,
        destination_type=destination_type,
        source_schema=schema,
        stage_version=stage_version,
        prompt=prompt,
        mapping_context_summary=mapping_context_summary,
    )


def schedule_migration_outcome(
    *,
    user_id: str,
    conversation_id: Optional[str],
    batch_id: str,
    total_records: int,
    successful_records: int,
    failed_records: int,
    failed_fields: Optional[List[str]] = None,
    error_samples: Optional[List[str]] = None,
    destination_type: Optional[str] = None,
) -> None:
    PipelineLearningService.schedule_capture(
        lambda: PipelineLearningService.capture_migration_outcome(
            user_id=user_id,
            conversation_id=conversation_id,
            batch_id=batch_id,
            total_records=total_records,
            successful_records=successful_records,
            failed_records=failed_records,
            failed_fields=failed_fields,
            error_samples=error_samples,
            destination_type=destination_type,
        )
    )
