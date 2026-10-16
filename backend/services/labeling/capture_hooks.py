"""Thin hooks for label learning capture (non-blocking)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.labeling.label_learning_service import LabelLearningService


def schedule_plan_label_evidence(
    *,
    user_id: str,
    conversation_id: Optional[str],
    prompt: str,
    classification: Dict[str, Any],
    event_type: str = "plan_approved",
) -> None:
    LabelLearningService.schedule_capture(
        lambda: LabelLearningService.capture_plan_event(
            user_id=str(user_id),
            conversation_id=conversation_id,
            prompt=prompt,
            classification=dict(classification or {}),
            event_type=event_type,
        )
    )


def schedule_clarification_label_evidence(
    *,
    user_id: str,
    conversation_id: Optional[str],
    prompt: Optional[str],
    classification: Dict[str, Any],
    selections: List[Dict[str, Any]],
) -> None:
    LabelLearningService.schedule_capture(
        lambda: LabelLearningService.capture_clarification_resolved(
            user_id=str(user_id),
            conversation_id=conversation_id,
            prompt=prompt,
            classification=dict(classification or {}),
            selections=selections or [],
        )
    )


def schedule_mapping_label_evidence(
    *,
    user_id: str,
    conversation_id: Optional[str],
    mapping_config: Dict[str, Any],
    destination_object: Optional[str] = None,
    destination_type: Optional[str] = None,
    destination_name: Optional[str] = None,
    connector_id: Optional[str] = None,
    source_type: Optional[str] = None,
    source_name: Optional[str] = None,
) -> None:
    LabelLearningService.schedule_capture(
        lambda: LabelLearningService.capture_mapping_finalized(
            user_id=str(user_id),
            conversation_id=conversation_id,
            mapping_config=mapping_config or {},
            destination_object=destination_object,
            destination_type=destination_type,
            destination_name=destination_name,
            connector_id=connector_id,
            source_type=source_type,
            source_name=source_name,
        )
    )


def schedule_migration_label_evidence(
    *,
    user_id: str,
    conversation_id: Optional[str],
    batch_id: str,
    destination_type: Optional[str] = None,
    destination_name: Optional[str] = None,
    total_records: int = 0,
    successful_records: int = 0,
    failed_records: int = 0,
    destination_object: Optional[str] = None,
    failed_fields: Optional[List[str]] = None,
    error_samples: Optional[List[str]] = None,
) -> None:
    LabelLearningService.schedule_capture(
        lambda: LabelLearningService.capture_migration_outcome(
            user_id=str(user_id),
            conversation_id=conversation_id,
            batch_id=batch_id,
            destination_type=destination_type,
            destination_name=destination_name,
            total_records=total_records,
            successful_records=successful_records,
            failed_records=failed_records,
            destination_object=destination_object,
            failed_fields=failed_fields,
            error_samples=error_samples,
        )
    )
