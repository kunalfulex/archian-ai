"""Recovery thresholds and helpers — code constants (always on)."""
from __future__ import annotations

import os
from typing import Literal, Optional

from agent.observation import ExecutionObservation, FailureReport

# Success rate at or above → no recovery loop.
MIN_SUCCESS_RATE_OK = 0.95

# Below this → stop and ask user (catastrophic failure).
MIN_SUCCESS_RATE_DIAGNOSE = 0.50

MAX_RECOVERY_ITERATIONS = 3
MAX_DYNAMIC_STEPS = 5
MAX_UNIVERSAL_STEPS = int(os.getenv("AGENT_MAX_UNIVERSAL_STEPS", "12"))
MAX_PICK_RETRIES = int(os.getenv("AGENT_MAX_PICK_RETRIES", "1"))

RUNTIME_CAPABILITIES = {
    "observe": "on",
    "failure_rag_capture": "on",
    "recovery_loop": "on",
    "recovery_writes": "client_approval",
}


def migration_chunk_type(success_rate: float) -> str:
    if success_rate >= MIN_SUCCESS_RATE_OK:
        return "migration_outcome"
    if success_rate >= MIN_SUCCESS_RATE_DIAGNOSE:
        return "migration_partial"
    return "migration_failed"


def migration_event_type(success_rate: float) -> str:
    if success_rate >= MIN_SUCCESS_RATE_OK:
        return "migration_success"
    if success_rate >= MIN_SUCCESS_RATE_DIAGNOSE:
        return "migration_partial"
    return "migration_failed"


TERMINAL_BATCH_STATUSES = frozenset({"completed", "failed"})


def migration_batch_status_is_terminal(status: Optional[str]) -> bool:
    """Recovery may only diagnose batches that finished (migration_batches.status)."""
    return (status or "").strip().lower() in TERMINAL_BATCH_STATUSES


def migration_batch_in_progress(
    *,
    batch_status: Optional[str] = None,
    total_records: int,
    successful_records: int,
    failed_records: int,
) -> bool:
    """True when the batch is not terminal in DB or record outcomes are incomplete."""
    if batch_status and not migration_batch_status_is_terminal(batch_status):
        return True
    total = max(int(total_records or 0), 0)
    if total <= 0:
        return True
    processed = int(successful_records or 0) + int(failed_records or 0)
    return processed < total


def should_recover(observation: ExecutionObservation) -> bool:
    """Legacy gate: success rate below OK threshold (used for learning chunk types)."""
    if observation.status == "submitted":
        return False
    report = observation.failure_report
    if not report:
        return observation.status in ("partial", "failed")
    if migration_batch_in_progress(
        batch_status=(observation.metrics or {}).get("batch_status"),
        total_records=report.total_records,
        successful_records=report.successful_records,
        failed_records=report.failed_count,
    ):
        return False
    return report.success_rate < MIN_SUCCESS_RATE_OK


def should_offer_failure_summary(observation: ExecutionObservation) -> bool:
    """True when a terminal batch has at least one failed record."""
    if observation.status == "submitted":
        return False
    report = observation.failure_report
    if not report:
        return observation.status in ("partial", "failed")
    if migration_batch_in_progress(
        batch_status=(observation.metrics or {}).get("batch_status"),
        total_records=report.total_records,
        successful_records=report.successful_records,
        failed_records=report.failed_count,
    ):
        return False
    return int(report.failed_count or 0) > 0


def should_offer_write_recovery(
    observation: ExecutionObservation,
    *,
    recoverable_count: int = 0,
) -> bool:
    """True when failed rows may be fixed via re-map and retry (not inform-only)."""
    if int(recoverable_count or 0) <= 0:
        return False
    if observation.status == "submitted":
        return False
    report = observation.failure_report
    if not report:
        return False
    if migration_batch_in_progress(
        batch_status=(observation.metrics or {}).get("batch_status"),
        total_records=report.total_records,
        successful_records=report.successful_records,
        failed_records=report.failed_count,
    ):
        return False
    return True


def severity(
    observation: ExecutionObservation,
) -> Literal["ok", "partial", "failed", "unknown"]:
    report = observation.failure_report
    if report:
        return report.severity
    if observation.status == "failed":
        return "failed"
    if observation.status == "partial":
        return "partial"
    if observation.status == "success":
        return "ok"
    return "unknown"


def observation_status_from_rate(success_rate: float) -> str:
    if success_rate >= MIN_SUCCESS_RATE_OK:
        return "success"
    if success_rate >= MIN_SUCCESS_RATE_DIAGNOSE:
        return "partial"
    return "failed"
