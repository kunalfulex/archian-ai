"""Step identifiers and pipeline result types."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.intent.state import IntentState


class IntentStepId(str, Enum):
    S01_CONTEXT = "S01_context"
    S02_TURN_TYPE = "S02_turn_type"
    S03_GOAL = "S03_goal"
    S04_DESTINATION_NEED = "S04_destination_need"
    S05_SYSTEM_TYPES = "S05_system_types"
    S06_BIND_RESOURCES = "S06_bind_resources"
    S07_ENTITY = "S07_entity"
    S08_FIELD_SCOPE = "S08_field_scope"
    S09_OPERATIONS = "S09_operations"
    S10_GAPS = "S10_gaps"
    S11_PLAN_SKETCH = "S11_plan_sketch"
    S12_READY = "S12_ready"


class TurnType(str, Enum):
    GENERAL = "general"
    INFO = "info"
    EXTRACT = "extract"
    DATA_OPS = "data_ops"
    MAP = "map"
    MIGRATE = "migrate"
    METADATA_DEPLOY = "metadata_deploy"
    DEPLOY = "deploy"
    JOIN = "join"
    CREATE = "create"
    UPSERT = "upsert"
    UPDATE = "update"
    SEARCH = "search"
    UNKNOWN = "unknown"


class DestinationNeed(str, Enum):
    NONE = "none"
    REQUIRED = "required"
    INHERIT_SESSION = "inherit_session"


class RouteTarget(str, Enum):
    GENERAL_CHAT = "general_chat"
    INFO_QUERY = "info_query"
    PLANNER = "planner"
    AGENT_RUNTIME = "agent_runtime"


class IntentGap(BaseModel):
    step_id: IntentStepId
    clarification_type: Optional[str] = None
    message: Optional[str] = None


class StepOutcome(str, Enum):
    COMPLETE = "complete"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    DELEGATED = "delegated"


class StepResult(BaseModel):
    step_id: IntentStepId
    outcome: StepOutcome = StepOutcome.COMPLETE
    message: Optional[str] = None


class IntentPipelineResult(BaseModel):
    state: IntentState
    steps_completed: List[IntentStepId] = Field(default_factory=list)
    step_results: List[StepResult] = Field(default_factory=list)
    stopped_at: Optional[IntentStepId] = None
    route: RouteTarget = RouteTarget.PLANNER
    metrics: Optional[Any] = None  # IntentTurnMetrics, lazy import to avoid cycles

    def snapshot(self) -> Dict[str, Any]:
        out = {
            "steps_completed": [s.value for s in self.steps_completed],
            "stopped_at": self.stopped_at.value if self.stopped_at else None,
            "route": self.route.value,
            "readiness": self.state.readiness,
        }
        if self.metrics is not None and hasattr(self.metrics, "summary"):
            out["metrics"] = self.metrics.summary()
        return out
