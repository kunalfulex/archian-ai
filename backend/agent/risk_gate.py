"""Risk-tier approval gates for Agent Runtime v4."""
from __future__ import annotations

from typing import Any, Dict, Optional

from agent.session import AgentSession
from agent.tool_spec import ToolSpec, spec_for_tool
from agent.tools.base import Tool


def requires_approval(spec: ToolSpec) -> bool:
    return spec.requires_approval


def is_write_or_deploy_tier(risk_tier: str) -> bool:
    return risk_tier in ("write", "deploy")


def approval_granted(session: AgentSession, tool_id: str) -> bool:
    """True when session already has approval for this write/deploy tool."""
    snap = session.classification_snapshot or {}
    if tool_id == "map_fields" and snap.get("recovery_approved"):
        return True
    if tool_id == "start_migration" and snap.get("recovery_approved"):
        return True
    if tool_id == "metadata_deploy_salesforce" and snap.get("schema_deploy_approved"):
        return True
    if tool_id == "schema_gate_propose" and snap.get("schema_deploy_approved"):
        return True
    plan_status = (snap.get("plan_status") or "").lower()
    if plan_status == "approved" and tool_id in ("map_fields", "start_migration"):
        return True
    return False


def check_risk_gate(
    tool: Tool,
    session: AgentSession,
    args: Optional[Dict[str, Any]] = None,
    *,
    spec: Optional[ToolSpec] = None,
) -> tuple[bool, Optional[str]]:
    """
    Returns (allowed, block_reason).
    read/transform → allowed. write/deploy → allowed only if approval_granted.
    """
    tool_spec = spec or spec_for_tool(tool)
    if not requires_approval(tool_spec):
        return True, None
    if approval_granted(session, tool.tool_id):
        return True, None
    label = tool_spec.risk_tier
    return False, (
        f"Tool {tool.tool_id} requires user approval ({label} tier) before execution."
    )
