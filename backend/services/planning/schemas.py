"""Execution plan and clarification schemas (planning pipeline)."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


ConnectorType = Literal[
    "salesforce",
    "google_sheets",
    "sql",
    "file",
    "unknown",
]

PlanStatus = Literal[
    "draft",
    "awaiting_clarification",
    "awaiting_approval",
    "approved",
    "running",
    "completed",
    "failed",
    "cancelled",
]

def normalize_connector_type(value: Optional[str]) -> str:
    """Map legacy/alias connector labels to canonical ConnectorType values."""
    from services.planning.connector_guards import canonicalize_connector

    return canonicalize_connector(value)


def normalize_classification_connectors(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize source/destination and nested extract source connector_type fields."""
    out = dict(classification or {})
    for key in ("source", "destination"):
        if out.get(key):
            out[key] = normalize_connector_type(str(out[key]))
    for list_key in ("extract_sources", "planner_additional_sources"):
        items = out.get(list_key)
        if not isinstance(items, list):
            continue
        normalized: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            if row.get("connector_type"):
                row["connector_type"] = normalize_connector_type(str(row["connector_type"]))
            normalized.append(row)
        out[list_key] = normalized
    return out


def _coerce_connector_type(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_connector_type(value)
    return value


class StepType(str, Enum):
    EXTRACT = "extract"
    JOIN = "join"
    TRANSFORM = "transform"
    FILTER = "filter"
    MAP = "map"
    MIGRATE = "migrate"
    CREATE = "create"
    CREATE_SPREADSHEET = "create_spreadsheet"
    DEPLOY = "deploy_schema"
    DEPLOY_SCHEMA = "deploy_schema"
    UPSERT = "upsert"
    UPDATE = "update"
    SEARCH = "search"
    FORMULA = "formula"
    SAVE_FLOW = "save_flow"
    LOAD_FLOW = "load_flow"


class ResourceRef(BaseModel):
    """A resolved connector resource (names must exist in user catalog)."""

    connector_type: ConnectorType
    display_name: str

    @field_validator("connector_type", mode="before")
    @classmethod
    def _normalize_connector_type_field(cls, value: Any) -> Any:
        return _coerce_connector_type(value)
    connector_id: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    sheet_name: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class PlanStep(BaseModel):
    step_type: StepType
    label: Optional[str] = None
    source: Optional[ResourceRef] = None
    destination: Optional[ResourceRef] = None
    depends_on: List[str] = Field(default_factory=list)
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ClarificationOption(BaseModel):
    id: str
    label: str
    resource_ref: Optional[ResourceRef] = None
    create_new: bool = False


class Clarification(BaseModel):
    id: str
    clarification_type: str
    prompt: str
    options: List[ClarificationOption] = Field(default_factory=list)
    required: bool = True
    selected_option_id: Optional[str] = None
    context_summary: Optional[str] = None


class ExecutionPlan(BaseModel):
    plan_id: Optional[str] = None
    status: PlanStatus = "draft"
    goal_summary: Optional[str] = None
    steps: List[PlanStep] = Field(default_factory=list)
    clarifications: List[Clarification] = Field(default_factory=list)
    sources: List[ResourceRef] = Field(default_factory=list)
    destination: Optional[ResourceRef] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def blocking_clarifications(self) -> List[Clarification]:
        return [c for c in self.clarifications if c.required and not c.selected_option_id]


class AmbiguousMatch(BaseModel):
    """Catalog entries that matched a user phrase with insufficient confidence to pick one."""

    phrase: str
    connector_type: ConnectorType
    candidates: List[ResourceRef] = Field(default_factory=list)

    @field_validator("connector_type", mode="before")
    @classmethod
    def _normalize_connector_type_field(cls, value: Any) -> Any:
        return _coerce_connector_type(value)


class ResolvedEndpoint(BaseModel):
    """A catalog-grounded source or destination endpoint."""

    connector_type: ConnectorType
    display_name: str

    @field_validator("connector_type", mode="before")
    @classmethod
    def _normalize_connector_type_field(cls, value: Any) -> Any:
        return _coerce_connector_type(value)
    sheet_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    connector_id: Optional[str] = None

    def to_resource_ref(self) -> ResourceRef:
        return ResourceRef(
            connector_type=self.connector_type,
            display_name=self.display_name,
            connector_id=self.connector_id,
            spreadsheet_id=self.spreadsheet_id,
            sheet_name=self.sheet_name,
        )


class EndpointSignals(BaseModel):
    """Structured endpoint hints from LLM classification or regex backfill."""

    destination_same_as_source: bool = False
    in_place_transform: bool = False
    source_phrase: Optional[str] = None
    destination_phrase: Optional[str] = None


class EndpointResolution(BaseModel):
    """Result of resolve_endpoints — uniform shape for all connectors."""

    source: Optional[ResolvedEndpoint] = None
    destination: Optional[ResolvedEndpoint] = None
    destination_same_as_source: bool = False
    in_place_transform: bool = False
    source_phrase: Optional[str] = None
    destination_phrase: Optional[str] = None
    ambiguous: List[AmbiguousMatch] = Field(default_factory=list)


EntityResolutionSource = Literal[
    "user_explicit",
    "semantics",
    "catalog_label",
    "goal_entity",
    "llm_bind",
    "destination_same_as_source",
    "plan_pinned",
    "pending",
]


class PlanEntityRef(BaseModel):
    """Resolved connector + entity for planning and execution."""

    connector_type: ConnectorType = "unknown"
    connector_id: Optional[str] = None
    display_name: Optional[str] = None
    entity_key: Optional[str] = None
    """Executable catalog id: Salesforce API name, SQL table, or spreadsheet_id::tab."""
    catalog_label: Optional[str] = None
    """Human label from synced catalog (salesforce_objects.label, etc.)."""
    entity_label: Optional[str] = None
    """Deprecated alias for catalog_label — kept for backward compatibility."""
    sheet_name: Optional[str] = None
    spreadsheet_id: Optional[str] = None
    resolution_source: EntityResolutionSource = "pending"
    confidence: float = 0.0

    @field_validator("connector_type", mode="before")
    @classmethod
    def _normalize_connector_type_field(cls, value: Any) -> Any:
        return _coerce_connector_type(value)

    def is_resolved(self) -> bool:
        return bool((self.entity_key or "").strip()) and self.resolution_source != "pending"

    def user_catalog_label(self) -> str:
        return (
            (self.catalog_label or self.entity_label or "").strip()
            or (self.entity_key or "").strip()
        )

    def execution_display(self) -> Optional[str]:
        """
        User-facing line: concrete object / table / tab from synced catalog.
        Semantic categories are not shown here.
        """
        key = (self.entity_key or "").strip()
        if not key:
            return None
        conn = (self.display_name or "").strip()
        ct = normalize_connector_type(self.connector_type or "unknown")
        cat_label = self.user_catalog_label()

        if ct == "google_sheets":
            tab = (self.sheet_name or "").strip()
            if not tab and "::" in key:
                tab = key.split("::", 1)[-1]
            target = tab or key
            if conn:
                return f"{conn} → tab `{target}`"
            return f"tab `{target}`"

        if ct == "sql":
            if conn:
                return f"{conn} → `{key}`"
            return f"`{key}`"

        if ct == "salesforce":
            if cat_label and cat_label.lower() != key.lower():
                entity_part = f"`{key}` ({cat_label})"
            else:
                entity_part = f"`{key}`"
            if conn:
                return f"{conn} → {entity_part}"
            return entity_part

        if conn:
            return f"{conn} → `{key}`"
        return f"`{key}`"

    def display_line(self) -> Optional[str]:
        """Alias for plan UI — always catalog-entity-first."""
        line = self.execution_display()
        if not line:
            return None
        connector = (self.connector_type or "").replace("_", " ").strip()
        if connector and connector not in ("unknown",):
            return f"{line} ({connector})"
        return line

    def step_entity_label(self) -> str:
        """Compact entity name for plan step sentences (connector named separately)."""
        key = (self.entity_key or "").strip()
        if not key:
            return ""
        ct = normalize_connector_type(self.connector_type or "unknown")
        cat = self.user_catalog_label()

        if ct == "google_sheets":
            tab = (self.sheet_name or "").strip()
            if not tab and "::" in key:
                tab = key.split("::", 1)[-1]
            return tab or cat or key

        if ct == "salesforce":
            if cat and cat.lower() != key.lower():
                return f"{key} ({cat})"
            return key

        return key


class PlanContract(BaseModel):
    """Pinned source/destination entities for an approved execution plan."""

    source: PlanEntityRef = Field(default_factory=PlanEntityRef)
    destination: PlanEntityRef = Field(default_factory=PlanEntityRef)
    mapping_target: Optional[PlanEntityRef] = None
    entities_from_goal: List[str] = Field(default_factory=list)
    migration_intent: Optional[Dict[str, Any]] = None

    def mapping_entity(self) -> PlanEntityRef:
        return self.mapping_target or self.destination

# --- Classification JSON helpers (merged P4.9) ---

"""Strip non-JSON-serializable values from planner/classification payloads."""

import base64
import uuid
from datetime import date, datetime
from typing import Any, Dict


def _json_safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return {
            "_bytes_omitted": True,
            "size": len(value),
            "encoding": "base64",
            "preview_base64": base64.b64encode(value[:64]).decode("ascii") if value else "",
        }
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def json_safe_value(value: Any) -> Any:
    """Recursively convert a structure to JSON-serializable form."""
    if isinstance(value, dict):
        return {k: json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(v) for v in value]
    return _json_safe_scalar(value)


def sanitize_classification_for_json(classification: Dict[str, Any]) -> Dict[str, Any]:
    """
    Copy classification for SSE, chat metadata, and planner_pending.
    Drops raw file bytes; keeps _pending_file_ref metadata when present.
    """
    out = json_safe_value(dict(classification or {}))
    if not isinstance(out, dict):
        return {}
    out.pop("_pending_file_payload", None)
    return out

# --- Legacy plan adapter (merged P4.9) ---

"""Map ExecutionPlan / classification to legacy routing dicts."""

from typing import Any, Dict, Optional


def classification_from_plan(plan: ExecutionPlan) -> Dict[str, Any]:
    """Minimal legacy view for existing routers."""
    dest = plan.destination
    src = plan.sources[0] if plan.sources else None
    out: Dict[str, Any] = {
        "source": src.connector_type if src else "unknown",
        "destination": dest.connector_type if dest else "unknown",
        "source_name": src.display_name if src else None,
        "destination_name": dest.display_name if dest else None,
        "intent": "extract",
        "mode": "extract",
        "confidence": 0.9,
        "reasoning": plan.goal_summary or "execution plan",
    }
    if plan.metadata.get("classification"):
        base = dict(plan.metadata["classification"])
        base.update(
            {k: v for k, v in out.items() if v and v != "unknown"},
        )
        return base
    return out


def merge_plan_into_classification(
    classification: Dict[str, Any],
    plan: Optional[ExecutionPlan],
) -> Dict[str, Any]:
    out = dict(classification or {})
    if not plan:
        return out
    out["execution_plan"] = plan.model_dump(mode="json")
    out["plan_id"] = plan.plan_id
    out["plan_status"] = plan.status
    if not (plan.steps or []) and (plan.status or "") == "awaiting_approval":
        out["plan_status"] = "draft"
    blocking = plan.blocking_clarifications()
    if blocking:
        out["needs_clarification"] = True
        out["clarifications"] = [c.model_dump(mode="json") for c in blocking]
    elif not classification.get("needs_clarification"):
        out["needs_clarification"] = False
        out.pop("clarifications", None)
    if plan.destination:
        out["plan_destination"] = {
            "connector_type": plan.destination.connector_type,
            "display_name": plan.destination.display_name,
            "sheet_name": plan.destination.sheet_name,
        }
        out["destination_name"] = out.get("destination_name") or plan.destination.display_name
        if plan.destination.sheet_name:
            out["destination_sheet_name"] = (
                out.get("destination_sheet_name") or plan.destination.sheet_name
            )
    if plan.goal_summary:
        out["goal_summary"] = plan.goal_summary
    plan_contract = (plan.metadata or {}).get("plan_contract") or out.get("plan_contract")
    if plan_contract:
        out["plan_contract"] = plan_contract
    raw_intent = (plan.metadata or {}).get("migration_intent")
    if isinstance(raw_intent, dict):
        out["migration_intent"] = raw_intent
        if raw_intent.get("extract_fields_confirmed"):
            out["extract_fields_confirmed"] = True
    if plan.steps:
        out["plan_steps_summary"] = [
            {
                "step_type": s.step_type,
                "label": s.label,
                "tool_id": (s.parameters or {}).get("tool_id"),
                "destination_name": (
                    s.destination.display_name if s.destination else None
                ),
                "source_entity_key": (s.parameters or {}).get("source_entity_key"),
                "destination_entity_key": (s.parameters or {}).get("destination_entity_key"),
                "source_entity_label": (s.parameters or {}).get("source_entity_label"),
                "destination_entity_label": (s.parameters or {}).get("destination_entity_label"),
            }
            for s in plan.steps
        ]
    raw_goal = (plan.metadata or {}).get("goal")
    if isinstance(raw_goal, dict) and raw_goal:
        out["_plan_scope"] = raw_goal
    return out

