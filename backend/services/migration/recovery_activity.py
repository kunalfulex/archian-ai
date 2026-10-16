"""Recovery analysis activity bus — append-only milestones during post-migrate recovery."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

recovery_activity_bus_var: ContextVar[Optional["RecoveryActivityBus"]] = ContextVar(
    "recovery_activity_bus",
    default=None,
)


@dataclass
class RecoveryActivityRecord:
    activity_id: str
    label: str
    status: str
    detail: Optional[str] = None


class RecoveryActivityBus:
    """Queue of recovery milestones for the current migration SSE recovery phase."""

    def __init__(self) -> None:
        self._pending: List[RecoveryActivityRecord] = []

    def emit(
        self,
        activity_id: str,
        label: str,
        status: str,
        *,
        detail: Optional[str] = None,
    ) -> None:
        if not activity_id or not label:
            return
        self._pending.append(
            RecoveryActivityRecord(
                activity_id=str(activity_id),
                label=str(label).strip(),
                status=(status or "running").strip().lower(),
                detail=(detail or "").strip() or None,
            )
        )

    def drain(self) -> List[RecoveryActivityRecord]:
        items = list(self._pending)
        self._pending.clear()
        return items


def install_recovery_activity_bus() -> Token:
    return recovery_activity_bus_var.set(RecoveryActivityBus())


def reset_recovery_activity_bus(token: Token) -> None:
    recovery_activity_bus_var.reset(token)


def emit_recovery_activity(
    activity_id: str,
    label: str,
    status: str,
    *,
    detail: Optional[str] = None,
) -> None:
    bus = recovery_activity_bus_var.get()
    if bus is None:
        return
    bus.emit(activity_id, label, status, detail=detail)


def recovery_activity_payload(
    *,
    job_id: str,
    batch_id: Optional[str],
    activity_id: str,
    label: str,
    status: str,
    detail: Optional[str] = None,
) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "event_type": "recovery_activity",
        "job_id": job_id,
        "activity_id": activity_id,
        "label": label,
        "status": status,
    }
    if batch_id:
        body["batch_id"] = batch_id
    if detail:
        body["detail"] = detail
    return body


def drain_recovery_activity_payloads(
    *,
    job_id: str,
    batch_id: Optional[str],
) -> List[Dict[str, Any]]:
    bus = recovery_activity_bus_var.get()
    if bus is None:
        return []
    payloads: List[Dict[str, Any]] = []
    for record in bus.drain():
        payloads.append(
            recovery_activity_payload(
                job_id=job_id,
                batch_id=batch_id,
                activity_id=record.activity_id,
                label=record.label,
                status=record.status,
                detail=record.detail,
            )
        )
    return payloads
