"""Precondition checks before dynamic/recovery tool invoke.

Planning-time slot requirements live in ``services.planning.op_requirements``;
``TOOL_PRECONDITIONS`` / rules here are the execution-time safety net for the same
constraints (e.g. ``requires_active_job_id`` ↔ ``active_job`` slot,
``requires_mapping`` ↔ ``mapping_ready``, ``requires_destination`` ↔
``destination_connector``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent.session import AgentSession
from agent.tool_spec import preconditions_for_tool_id


@dataclass
class PreconditionResult:
    ok: bool
    failed: List[str]
    message: Optional[str] = None


def _has_mapping(session: AgentSession, args: Dict[str, Any]) -> bool:
    if args.get("mapping_id") or args.get("mapping_config"):
        return True
    if session.mapping_id or session.mapping_config:
        return True
    last = session.last_result or {}
    if last.get("mapping_id") or last.get("mapping_config"):
        return True
    mapping = last.get("mapping")
    if isinstance(mapping, dict) and mapping:
        return True
    return False


def _batch_id(session: AgentSession, args: Dict[str, Any]) -> Optional[str]:
    if args.get("batch_id"):
        return str(args["batch_id"])
    last = session.last_result or {}
    if last.get("batch_id"):
        return str(last["batch_id"])
    snap = session.classification_snapshot or {}
    fr = snap.get("last_failure_report") or {}
    if isinstance(fr, dict) and fr.get("batch_id"):
        return str(fr["batch_id"])
    return None


def _destination(session: AgentSession) -> Optional[str]:
    dest = session.destination or (session.classification_snapshot or {}).get("destination")
    if dest and str(dest).strip().lower() not in ("", "unknown"):
        return str(dest)
    return None


def _schema_deploy_blocked(session: AgentSession) -> bool:
    snap = session.classification_snapshot or {}
    missing = snap.get("schema_gate_missing_fields") or []
    if not missing:
        return False
    return not snap.get("schema_deploy_approved")


def check_preconditions(
    tool_id: str,
    session: AgentSession,
    args: Optional[Dict[str, Any]] = None,
    *,
    required: Optional[List[str]] = None,
) -> PreconditionResult:
    from agent.tool_manifest import TOOL_PRECONDITIONS

    args = dict(args or {})
    rules = required
    if rules is None:
        rules = TOOL_PRECONDITIONS.get(tool_id) or preconditions_for_tool_id(tool_id)
    failed: List[str] = []

    for rule in rules:
        if rule == "requires_active_job_id":
            job = args.get("job_id") or args.get("parent_job_id") or session.active_job_id
            if not job:
                failed.append(rule)
        elif rule == "requires_mapping":
            if not _has_mapping(session, args):
                failed.append(rule)
        elif rule == "requires_batch_id":
            if not _batch_id(session, args):
                failed.append(rule)
        elif rule == "requires_conversation_id":
            if not session.conversation_id:
                failed.append(rule)
        elif rule == "requires_destination":
            if not _destination(session):
                failed.append(rule)
        elif rule == "requires_schema_deploy_when_pending":
            if _schema_deploy_blocked(session):
                failed.append(rule)

    if failed:
        return PreconditionResult(
            ok=False,
            failed=failed,
            message=f"Preconditions not met for {tool_id}: {', '.join(failed)}",
        )
    return PreconditionResult(ok=True, failed=[])
