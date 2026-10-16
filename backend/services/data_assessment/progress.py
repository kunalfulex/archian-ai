"""Shared progress payloads for connector data assessment jobs."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Phases surfaced to UI (connector + enterprise graph enrichment).
PHASE_LISTING = "listing"
PHASE_PROFILING = "profiling"
PHASE_SAVING = "saving"
PHASE_METADATA_GAPS = "metadata_gaps"
PHASE_SYNTHESIZING = "synthesizing"
PHASE_ATTACHING_GRAPH = "attaching_graph"
PHASE_FINALIZING = "finalizing"

_PHASE_LABELS = {
    PHASE_LISTING: "Listing objects",
    PHASE_PROFILING: "Profiling",
    PHASE_SAVING: "Saving partial report",
    PHASE_METADATA_GAPS: "Analyzing metadata gaps",
    PHASE_SYNTHESIZING: "Generating report",
    PHASE_ATTACHING_GRAPH: "Attaching schema graph",
    PHASE_FINALIZING: "Finalizing report",
}


def assessment_progress_heartbeat_seconds() -> float:
    try:
        return max(1.0, float(os.getenv("ASSESSMENT_PROGRESS_HEARTBEAT_SECONDS", "2")))
    except ValueError:
        return 2.0


def sub_phase_label(sub_phase: Optional[str]) -> str:
    if not sub_phase:
        return ""
    return _PHASE_LABELS.get(str(sub_phase), str(sub_phase).replace("_", " ").title())


def entity_work_percent(
    completed_entities: int,
    total_entities: int,
    *,
    in_entity: bool = False,
) -> int:
    """Map entity loop position to 5–92% (room left for post-entity phases)."""
    if total_entities <= 0:
        return 5
    slot = completed_entities + (0.45 if in_entity else 0.0)
    return min(92, max(5, int((slot / total_entities) * 87) + 5))


def phase_percent(
    sub_phase: str,
    *,
    completed_entities: int = 0,
    total_entities: int = 0,
    in_entity: bool = False,
) -> int:
    if sub_phase == PHASE_LISTING:
        return 3
    if sub_phase == PHASE_PROFILING:
        return entity_work_percent(
            completed_entities, total_entities, in_entity=in_entity
        )
    if sub_phase == PHASE_SAVING:
        return entity_work_percent(completed_entities + 1, total_entities)
    if sub_phase == PHASE_METADATA_GAPS:
        return 93
    if sub_phase == PHASE_SYNTHESIZING:
        return 96
    if sub_phase == PHASE_ATTACHING_GRAPH:
        return 98
    if sub_phase == PHASE_FINALIZING:
        return 99
    return entity_work_percent(completed_entities, total_entities, in_entity=in_entity)


def build_progress_payload(
    *,
    sub_phase: str,
    completed_entities: int = 0,
    total_entities: int = 0,
    current_entity: Optional[str] = None,
    in_entity: bool = False,
    heartbeat: bool = False,
) -> Dict[str, Any]:
    percent = phase_percent(
        sub_phase,
        completed_entities=completed_entities,
        total_entities=total_entities,
        in_entity=in_entity,
    )
    label = sub_phase_label(sub_phase)
    message_parts = [label]
    if current_entity and sub_phase == PHASE_PROFILING:
        message_parts.append(current_entity)
    elif total_entities > 0 and sub_phase in (PHASE_PROFILING, PHASE_SAVING):
        done = completed_entities + (1 if sub_phase == PHASE_SAVING else 0)
        message_parts.append(f"{min(done, total_entities)}/{total_entities}")

    payload: Dict[str, Any] = {
        "sub_phase": sub_phase,
        "percent": percent,
        "completed_entities": completed_entities,
        "total_entities": total_entities,
        "current_entity": current_entity,
        "message": " — ".join(message_parts),
    }
    if heartbeat:
        payload["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
    return payload
