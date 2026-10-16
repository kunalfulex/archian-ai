"""Types for Connector Metadata Agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

ConnectorType = Literal["salesforce", "sql", "google_sheets"]
SyncMode = Literal["schema_only", "schema_and_categorize", "full"]
DriftKind = Literal["new_entity", "schema_changed", "removed_entity", "unchanged"]


@dataclass
class DriftEntry:
    entity_id: str
    kind: DriftKind
    previous_hash: Optional[str] = None
    current_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "kind": self.kind,
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
        }


@dataclass
class DriftReport:
    new_entities: List[str] = field(default_factory=list)
    changed_entities: List[str] = field(default_factory=list)
    removed_entities: List[str] = field(default_factory=list)
    unchanged_count: int = 0
    entries: List[DriftEntry] = field(default_factory=list)

    def entities_needing_categorization(self, sync_mode: str) -> List[str]:
        if sync_mode == "schema_only":
            return []
        if sync_mode == "full":
            return [
                e.entity_id
                for e in self.entries
                if e.kind in ("new_entity", "schema_changed", "unchanged")
            ]
        return self.new_entities + self.changed_entities

    def to_dict(self) -> Dict[str, Any]:
        return {
            "new_entities": self.new_entities,
            "changed_entities": self.changed_entities,
            "removed_entities": self.removed_entities,
            "unchanged_count": self.unchanged_count,
            "entries": [e.to_dict() for e in self.entries],
        }


@dataclass
class SchemaNode:
    id: str
    label: str
    node_type: str
    orphan_kind: Optional[str] = None
    is_deleted: bool = False
    gap_severities: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type,
            "orphan_kind": self.orphan_kind,
            "is_deleted": self.is_deleted,
            "gap_severities": self.gap_severities,
        }


@dataclass
class SchemaEdge:
    source_id: str
    target_id: str
    label: str
    edge_type: str
    gap_severity: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "label": self.label,
            "edge_type": self.edge_type,
            "gap_severity": self.gap_severity,
        }


@dataclass
class SchemaGraph:
    nodes: List[SchemaNode]
    edges: List[SchemaEdge]
    summary: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
            "summary": self.summary,
        }
