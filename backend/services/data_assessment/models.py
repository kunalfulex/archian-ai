"""Types for connector data assessment."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ConnectorType = Literal["salesforce", "sql", "google_sheets"]
AssessmentScope = Literal["quick", "standard", "full"]
JobStatus = Literal["queued", "running", "paused", "completed", "failed", "cancelled"]


@dataclass
class EntityRef:
    """One assessable unit: SF object, SQL table, or sheet tab."""

    entity_id: str
    label: str
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"entity_id": self.entity_id, "label": self.label, "meta": self.meta}


@dataclass
class ObjectProfile:
    """Aggregated profile for one entity (no raw rows)."""

    entity_id: str
    label: str
    records_sampled: int
    quality_report: Dict[str, Any]
    dimension_scores: Dict[str, float]
    issues_summary: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "entity_id": self.entity_id,
            "label": self.label,
            "records_sampled": self.records_sampled,
            "quality_report": self._sanitized_quality_report(),
            "dimension_scores": self.dimension_scores,
            "issues_summary": self.issues_summary,
        }
        if self.error:
            out["error"] = self.error
        return out

    def _sanitized_quality_report(self) -> Dict[str, Any]:
        """Strip record indices from persisted profiles."""
        import copy

        qr = copy.deepcopy(self.quality_report or {})
        dups = qr.get("duplicates") or {}
        for key in ("exact_duplicates", "key_field_duplicates"):
            block = dups.get(key)
            if isinstance(block, dict) and "record_indices" in block:
                block.pop("record_indices", None)
        fmt = qr.get("format_issues") or {}
        for fk, issues in list(fmt.items()):
            if isinstance(issues, list) and len(issues) > 5:
                fmt[fk] = issues[:5]
        return qr


@dataclass
class GapFinding:
    entity_id: str
    severity: str
    category: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "severity": self.severity,
            "category": self.category,
            "message": self.message,
        }
