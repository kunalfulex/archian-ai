"""Tests for rule-first migration recovery error classification."""
from __future__ import annotations

from services.planning.recovery_error_classifier import (
    classify_failed_record,
    classify_failed_records,
)
from services.planning.recovery_error_classifier import RecoveryErrorCategory


def test_classify_storage_limit_as_system_not_recoverable():
    rec = classify_failed_record(
        record_index=3,
        error_message="STORAGE_LIMIT_EXCEEDED:storage limit exceeded",
    )
    assert rec.category == RecoveryErrorCategory.SYSTEM_LIMIT
    assert rec.recoverable is False


def test_classify_invalid_email_as_source_not_recoverable():
    rec = classify_failed_record(
        record_index=1,
        error_message="Record could not be matched to bulk API results",
        source_data={"Email": "b.levy@expressl&t.net", "LastName": "Levy"},
    )
    assert rec.category == RecoveryErrorCategory.INVALID_SOURCE_DATA
    assert rec.recoverable is False
    assert "Invalid email" in (rec.detail or "")


def test_classify_lookup_error_as_recoverable():
    rec = classify_failed_record(
        record_index=5,
        error_message="Foreign key external id not found for field AccountId",
    )
    assert rec.category == RecoveryErrorCategory.LOOKUP_RECOVERABLE
    assert rec.recoverable is True


def test_classify_mapping_required_field_as_recoverable():
    rec = classify_failed_record(
        record_index=2,
        error_message="Required fields are missing: [LastName]",
    )
    assert rec.category == RecoveryErrorCategory.MAPPING_RECOVERABLE
    assert rec.recoverable is True


def test_mixed_batch_summary():
    rows = [
        {
            "record_index": 0,
            "error_message": "STORAGE_LIMIT_EXCEEDED:storage limit exceeded",
            "source_data": {},
        },
        {
            "record_index": 1,
            "error_message": "Record could not be matched to bulk API results",
            "source_data": {"Email": "bad@dom&ain.net"},
        },
        {
            "record_index": 2,
            "error_message": "Foreign key external id not found for field AccountId",
            "source_data": {},
        },
    ]
    summary = classify_failed_records(rows, batch_id="batch-1")
    assert summary.total_failed == 3
    assert summary.recoverable_count == 1
    assert summary.unrecoverable_count == 2
    assert summary.recovery_action == "remap_and_retry"
    assert summary.recoverable_record_indices == [2]
