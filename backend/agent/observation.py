"""Structured execution observations for Agent Runtime v2."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


ObservationStatus = Literal["success", "partial", "failed", "skipped", "submitted"]


class TopError(BaseModel):
    message: str
    count: int = 1
    sample_record_index: Optional[int] = None


class FailureReport(BaseModel):
    batch_id: Optional[str] = None
    destination_type: Optional[str] = None
    destination_object: Optional[str] = None
    success_rate: float = 0.0
    failed_count: int = 0
    total_records: int = 0
    successful_records: int = 0
    top_errors: List[TopError] = Field(default_factory=list)
    failed_fields: List[str] = Field(default_factory=list)
    error_samples: List[str] = Field(default_factory=list)

    @property
    def severity(self) -> Literal["ok", "partial", "failed"]:
        if self.success_rate >= 0.95:
            return "ok"
        if self.success_rate >= 0.50:
            return "partial"
        return "failed"


class ExecutionObservation(BaseModel):
    step_id: Optional[str] = None
    tool_id: str
    status: ObservationStatus = "success"
    batch_id: Optional[str] = None
    job_id: Optional[str] = None
    mapping_id: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    failure_report: Optional[FailureReport] = None

    def model_dump_json_safe(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class RecoveryDecision(BaseModel):
    tool_id: Optional[str] = None
    arguments: Dict[str, Any] = Field(default_factory=dict)
    rationale: Optional[str] = None
    ask_user: bool = False
