"""Unified intent state mutated by pipeline steps S01–S12."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.planning.migration_intent_schemas import ExtractFieldScope
from services.planning.schemas import normalize_connector_type

Readiness = Literal[
    "pending",
    "need_clarification",
    "ready_to_plan",
    "ready_to_execute",
    "route_general",
]


class ConnectorRef(BaseModel):
    connector_type: str = "unknown"
    display_name: Optional[str] = None
    connector_id: Optional[str] = None
    sheet_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    resolved: bool = False


class EntityRef(BaseModel):
    entity_key: Optional[str] = None
    entity_label: Optional[str] = None
    confidence: float = 0.0
    resolution_source: Optional[str] = None


class FieldSpec(BaseModel):
    scope: ExtractFieldScope = "unspecified"
    scope_confidence: float = 0.0
    hints: List[str] = Field(default_factory=list)
    hints_strict: bool = False
    confirmed: bool = False


class IntentState(BaseModel):
    """
    Single source of truth for one user turn's thinking pipeline.

    Steps append to `steps_completed` and mutate fields; legacy classification
    is a projection via `project_to_classification`.
    """

    # Input
    prompt: str = ""
    original_prompt: Optional[str] = None
    user_id: Optional[str] = None
    conversation_id: Optional[str] = None

    # S01 — context
    fresh_extract: bool = False
    continuation_turn: bool = False
    active_job_id: Optional[str] = None
    session_action: Optional[str] = None
    continuation_phase: Optional[str] = None
    session_confidence: float = 0.0
    last_completed_step: Optional[str] = None

    # S02 — turn type
    turn_type: Optional[str] = None
    conversational: bool = False
    off_topic: bool = False

    # S03 — goal
    goal_summary: Optional[str] = None

    # S04 — destination need
    needs_destination: Optional[str] = None  # DestinationNeed value

    # S05 — system types
    source_type: Optional[str] = None
    destination_type: Optional[str] = None

    # S06 — resource bind
    source: ConnectorRef = Field(default_factory=ConnectorRef)
    destination: ConnectorRef = Field(default_factory=ConnectorRef)
    destination_same_as_source: bool = False

    # S07 — entity
    source_entity: EntityRef = Field(default_factory=EntityRef)

    # S08 — fields
    fields: FieldSpec = Field(default_factory=FieldSpec)

    # S09 — operations
    operations: List[str] = Field(default_factory=list)
    mode: Optional[str] = None

    # S10 — gaps
    gaps: List[Dict[str, Any]] = Field(default_factory=list)
    active_gap: Optional[str] = None
    gap_clarifications: List[Dict[str, Any]] = Field(default_factory=list)

    # S11 — plan
    plan_id: Optional[str] = None
    plan_status: Optional[str] = None
    plan_step_count: int = 0

    # S12 — ready
    readiness: Readiness = "pending"
    route: Optional[str] = None

    # Pipeline meta
    steps_completed: List[str] = Field(default_factory=list)
    step_timings_ms: Dict[str, float] = Field(default_factory=dict)
    classifier_snapshot: Dict[str, Any] = Field(default_factory=dict)

    def mark_step(self, step_id: str) -> None:
        if step_id not in self.steps_completed:
            self.steps_completed.append(step_id)

    def summary_snapshot(self) -> Dict[str, Any]:
        """Compact dict for logging."""
        return {
            "turn_type": self.turn_type,
            "goal_summary": (self.goal_summary or "")[:120] or None,
            "needs_destination": self.needs_destination,
            "source_type": self.source_type,
            "source_name": self.source.display_name,
            "destination_type": self.destination_type,
            "destination_name": self.destination.display_name,
            "source_entity": self.source_entity.entity_key,
            "field_scope": self.fields.scope,
            "field_hints": self.fields.hints[:5] if self.fields.hints else [],
            "operations": self.operations,
            "readiness": self.readiness,
            "route": self.route,
            "steps_completed": list(self.steps_completed),
        }

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        *,
        user_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        classification: Optional[Dict[str, Any]] = None,
    ) -> "IntentState":
        """Bootstrap state from prompt and optional legacy classifier output."""
        cls_dict = dict(classification or {})
        src_type = normalize_connector_type(cls_dict.get("source") or "")
        dest_type = normalize_connector_type(cls_dict.get("destination") or "")

        state = cls(
            prompt=(prompt or "").strip(),
            original_prompt=(prompt or "").strip(),
            user_id=user_id,
            conversation_id=conversation_id,
            classifier_snapshot={
                k: cls_dict.get(k)
                for k in (
                    "source",
                    "destination",
                    "source_name",
                    "destination_name",
                    "intent",
                    "mode",
                    "operations",
                    "confidence",
                    "reasoning",
                    "destination_phrase",
                    "destination_same_as_source",
                    "planner_destination_confirmed",
                    "planner_create_spreadsheet",
                    "multi_source_extract",
                )
                if cls_dict.get(k) is not None
            },
        )

        if src_type and src_type != "unknown":
            state.source_type = src_type
            state.source.connector_type = src_type
            state.source.display_name = (cls_dict.get("source_name") or "").strip() or None
            state.source.connector_id = (cls_dict.get("source_connector_id") or "").strip() or None
            state.source.resolved = bool(state.source.display_name or state.source.connector_id)

        if dest_type and dest_type != "unknown":
            state.destination_type = dest_type
            state.destination.connector_type = dest_type
            state.destination.display_name = (cls_dict.get("destination_name") or "").strip() or None
            state.destination.connector_id = (
                (cls_dict.get("destination_connector_id") or "").strip() or None
            )
            state.destination.resolved = bool(
                state.destination.display_name
                or state.destination.connector_id
                or cls_dict.get("planner_destination_confirmed")
                or cls_dict.get("planner_create_spreadsheet")
            )

        intent = (cls_dict.get("intent") or "").lower()
        state.turn_type = intent or None
        state.mode = cls_dict.get("mode")
        state.conversational = bool(cls_dict.get("conversational_turn"))
        state.off_topic = bool(cls_dict.get("off_topic"))
        state.goal_summary = (cls_dict.get("reasoning") or cls_dict.get("goal_summary") or "")[:200] or None
        state.operations = [str(o).lower() for o in (cls_dict.get("operations") or [])]
        state.destination_same_as_source = bool(cls_dict.get("destination_same_as_source"))

        table = (cls_dict.get("source_table_name") or cls_dict.get("source_object_api_name") or "").strip()
        if table:
            state.source_entity.entity_key = table
            state.source_entity.entity_label = table
            state.source_entity.confidence = 0.9

        mi = cls_dict.get("migration_intent") or {}
        if isinstance(mi, dict):
            scope = mi.get("extract_field_scope") or "unspecified"
            state.fields.scope = scope  # type: ignore[assignment]
            state.fields.scope_confidence = float(mi.get("field_scope_confidence") or 0.0)
            state.fields.hints = list(mi.get("field_hints") or [])
            state.fields.hints_strict = bool(mi.get("field_hints_strict"))
            state.fields.confirmed = bool(mi.get("extract_fields_confirmed"))
            key = (mi.get("driving_entity_key") or "").strip()
            if key:
                state.source_entity.entity_key = key
                state.source_entity.entity_label = (
                    mi.get("driving_entity_label") or key
                )

        return state
