"""Planning-stage activity bus — append-only milestones streamed during plan building."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

planning_activity_bus_var: ContextVar[Optional["PlanningActivityBus"]] = ContextVar(
    "planning_activity_bus",
    default=None,
)


@dataclass
class PlanningActivityRecord:
    activity_id: str
    label: str
    status: str
    detail: Optional[str] = None
    segment: Optional[str] = None


class PlanningActivityBus:
    """Thread/async-safe queue of planning milestones for the current architect turn."""

    def __init__(self) -> None:
        self._pending: List[PlanningActivityRecord] = []

    def emit(
        self,
        activity_id: str,
        label: str,
        status: str,
        *,
        detail: Optional[str] = None,
        segment: Optional[str] = None,
    ) -> None:
        if not activity_id or not label:
            return
        self._pending.append(
            PlanningActivityRecord(
                activity_id=str(activity_id),
                label=str(label).strip(),
                status=(status or "running").strip().lower(),
                detail=(detail or "").strip() or None,
                segment=(segment or "").strip() or None,
            )
        )

    def drain(self) -> List[PlanningActivityRecord]:
        items = list(self._pending)
        self._pending.clear()
        return items


def install_planning_activity_bus() -> Token:
    return planning_activity_bus_var.set(PlanningActivityBus())


def reset_planning_activity_bus(token: Token) -> None:
    planning_activity_bus_var.reset(token)


def emit_planning_activity(
    activity_id: str,
    label: str,
    status: str,
    *,
    detail: Optional[str] = None,
    segment: Optional[str] = None,
) -> None:
    bus = planning_activity_bus_var.get()
    if bus is None:
        return
    bus.emit(activity_id, label, status, detail=detail, segment=segment)


def drain_planning_activity_sse_lines() -> List[str]:
    bus = planning_activity_bus_var.get()
    if bus is None:
        return []
    from agent.events import planning_activity as planning_activity_event

    lines: List[str] = []
    for record in bus.drain():
        lines.append(
            planning_activity_event(
                record.activity_id,
                record.label,
                record.status,
                detail=record.detail,
                segment=record.segment,
            )
        )
    return lines


def _connector_display(source: str) -> str:
    s = (source or "").strip().lower()
    if s in ("google_sheets", "gsheets"):
        return "Google Sheets"
    if s == "salesforce":
        return "Salesforce"
    if s == "sql":
        return "SQL database"
    if s == "file":
        return "File"
    if not s or s == "unknown":
        return "Source"
    return s.replace("_", " ").title()


def format_source_detail(classification: Dict[str, Any]) -> Optional[str]:
    src = _connector_display(classification.get("source") or "")
    name = (classification.get("source_name") or "").strip()
    if name:
        return f"{src} ({name})"
    if src and src != "Source":
        return src
    return None


def format_destination_detail(classification: Dict[str, Any]) -> Optional[str]:
    dest = _connector_display(classification.get("destination") or "")
    contract_dest = classification.get("plan_contract") or {}
    if isinstance(contract_dest, dict):
        contract_display = (contract_dest.get("destination") or {}).get("display_name")
        if isinstance(contract_display, str) and contract_display.strip():
            return contract_display.strip()
    name = (classification.get("destination_name") or "").strip()
    if name:
        return f"{dest} ({name})" if dest and dest != "Source" else name
    if dest and dest not in ("Source", "Unknown"):
        return dest
    return None


def format_operations_detail(parsed_goal: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(parsed_goal, dict):
        return None
    ops = parsed_goal.get("operations") or []
    labels: List[str] = []
    for op in ops:
        if not isinstance(op, str):
            continue
        key = op.strip().lower()
        if not key:
            continue
        if key == "extract":
            labels.append("extract")
        elif key in ("filter", "transform", "dedupe", "validate", "cleanup"):
            labels.append(key)
        elif key in ("map", "migrate", "upsert", "create", "join"):
            labels.append(key)
        else:
            labels.append(key.replace("_", " "))
    if not labels:
        return None
    return ", ".join(labels)


def emit_plan_steps_from_plan(plan: Any) -> None:
    steps = getattr(plan, "steps", None) or []
    for index, step in enumerate(steps):
        label = (getattr(step, "label", None) or getattr(step, "tool_id", None) or "Step").strip()
        emit_planning_activity(
            f"plan_step_{index}",
            f"Adding step: {label}",
            "done",
            segment="plan_build",
        )


def planning_activity_sse_line(
    activity_id: str,
    label: str,
    status: str,
    *,
    detail: Optional[str] = None,
    segment: Optional[str] = None,
) -> str:
    from agent.events import planning_activity as planning_activity_event

    return planning_activity_event(
        activity_id,
        label,
        status,
        detail=detail,
        segment=segment,
    )


def _plan_step_sse_lines(plan: Any) -> List[str]:
    lines: List[str] = []
    steps = getattr(plan, "steps", None) or []
    for index, step in enumerate(steps):
        label = (getattr(step, "label", None) or getattr(step, "tool_id", None) or "Step").strip()
        lines.append(
            planning_activity_sse_line(
                f"plan_step_{index}",
                f"Adding step: {label}",
                "done",
                segment="plan_build",
            )
        )
    return lines


def terminal_planning_success_sse_lines(plan: Any) -> List[str]:
    """Emit after final plan steps are known (e.g. emit-path recovery)."""
    steps = getattr(plan, "steps", None) or []
    lines = _plan_step_sse_lines(plan)
    if steps:
        lines.append(
            planning_activity_sse_line(
                "plan_ready",
                f"Plan ready ({len(steps)} steps)",
                "done",
                segment="plan_build",
            )
        )
    lines.append(
        planning_activity_sse_line(
            "finalize_plan",
            "Finalizing execution plan",
            "done",
            segment="plan_build",
        )
    )
    lines.append(
        planning_activity_sse_line(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        )
    )
    return lines


def terminal_planning_clarification_sse_lines() -> List[str]:
    return [
        planning_activity_sse_line(
            "clarification_needed",
            "Need your input to continue",
            "done",
            segment="plan_build",
        ),
        planning_activity_sse_line(
            "finalize_plan",
            "Finalizing execution plan",
            "done",
            segment="plan_build",
        ),
        planning_activity_sse_line(
            "assemble_plan",
            "Assembling execution plan",
            "done",
            segment="plan_build",
        ),
    ]


def terminal_planning_failure_sse_lines() -> List[str]:
    return [
        planning_activity_sse_line(
            "plan_failed",
            "Could not assemble plan yet",
            "failed",
            segment="plan_build",
        ),
        planning_activity_sse_line(
            "finalize_plan",
            "Finalizing execution plan",
            "failed",
            segment="plan_build",
        ),
        planning_activity_sse_line(
            "assemble_plan",
            "Assembling execution plan",
            "failed",
            segment="plan_build",
        ),
    ]
