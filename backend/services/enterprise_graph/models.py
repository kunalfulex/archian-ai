"""Types for Enterprise Graph."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ConnectorType = Literal["salesforce", "sql", "google_sheets"]
MemberRole = Literal["source", "warehouse", "reference", "lake"]
GraphStatus = Literal["draft", "active", "archived"]
RunDepth = Literal["quick", "partial", "full"]


@dataclass
class MemberInput:
    connector_type: str
    connector_id: str
    role: str = "source"
    display_name: Optional[str] = None
    sort_order: int = 0

    def connector_ref(self) -> Dict[str, str]:
        from services.enterprise_graph.connector_ref import connector_ref_for_storage

        return connector_ref_for_storage(self.connector_type, self.connector_id)


@dataclass
class GraphMember:
    id: str
    graph_id: str
    connector_type: str
    connector_ref: Dict[str, Any]
    role: str
    display_name: str
    sort_order: int
    last_schema_sync_job_id: Optional[str] = None
    last_assessment_report_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "graph_id": self.graph_id,
            "connector_type": self.connector_type,
            "connector_ref": self.connector_ref,
            "role": self.role,
            "display_name": self.display_name,
            "sort_order": self.sort_order,
            "last_schema_sync_job_id": self.last_schema_sync_job_id,
            "last_assessment_report_id": self.last_assessment_report_id,
        }


@dataclass
class EnterpriseGraph:
    id: str
    user_id: str
    name: str
    description: Optional[str]
    status: str
    settings: Dict[str, Any] = field(default_factory=dict)
    latest_run_id: Optional[str] = None
    members: List[GraphMember] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "settings": self.settings,
            "latest_run_id": self.latest_run_id,
            "members": [m.to_dict() for m in self.members],
        }
