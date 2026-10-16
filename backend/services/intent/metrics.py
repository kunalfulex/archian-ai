"""Per-turn intent pipeline metrics (P5.2)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class IntentTurnMetrics(BaseModel):
    """Aggregated metrics for one intent pipeline invocation."""

    phase: str = "pre_planning"
    step_durations_ms: Dict[str, float] = Field(default_factory=dict)
    steps_completed: List[str] = Field(default_factory=list)
    gaps_count: int = 0
    active_gap: Optional[str] = None
    readiness: Optional[str] = None
    route: Optional[str] = None
    turn_type: Optional[str] = None

    def summary(self) -> Dict[str, Any]:
        total_ms = sum(self.step_durations_ms.values())
        return {
            "phase": self.phase,
            "steps_completed": list(self.steps_completed),
            "step_durations_ms": dict(self.step_durations_ms),
            "total_duration_ms": round(total_ms, 2),
            "gaps_count": self.gaps_count,
            "active_gap": self.active_gap,
            "readiness": self.readiness,
            "route": self.route,
            "turn_type": self.turn_type,
        }


def metrics_from_state(state: Any, *, phase: str) -> IntentTurnMetrics:
    snap = getattr(state, "model_dump", lambda mode="json": {})()
    timings = snap.get("step_timings_ms") if isinstance(snap, dict) else getattr(state, "step_timings_ms", {})
    return IntentTurnMetrics(
        phase=phase,
        step_durations_ms=dict(timings or {}),
        steps_completed=list(getattr(state, "steps_completed", []) or []),
        gaps_count=len(getattr(state, "gaps", []) or []),
        active_gap=getattr(state, "active_gap", None),
        readiness=getattr(state, "readiness", None),
        route=getattr(state, "route", None),
        turn_type=getattr(state, "turn_type", None),
    )


def detect_wrong_route(
    classification: Dict[str, Any],
    *,
    pre_intent: Optional[str] = None,
) -> Optional[str]:
    """
    Heuristic wrong-route signal for observability (P5.2).

    Returns a short reason when final route/turn_type likely mismatches classifier intent.
    """
    snap = classification.get("intent_state")
    if not isinstance(snap, dict):
        return None

    turn = (snap.get("turn_type") or "").lower()
    route = (snap.get("route") or "").lower()
    pre = (pre_intent or classification.get("intent") or "").lower()
    ops = [str(o).lower() for o in (snap.get("operations") or classification.get("operations") or [])]

    data_ops_turns = {"extract", "map", "migrate", "data_ops", "join", "metadata_deploy"}
    if pre in ("extract", "migrate_data", "migrate", "map_data") and turn == "general":
        return "classifier_data_ops_routed_general"
    if route == "general_chat" and turn in data_ops_turns and turn not in ("general", "info"):
        return "data_ops_routed_general_chat"
    if pre == "extract" and turn == "general" and "extract" in ops:
        return "extract_ops_with_general_turn"
    return None
