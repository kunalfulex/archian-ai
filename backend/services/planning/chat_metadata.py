"""Chat message metadata helpers for planner v2 pipeline state."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def format_plan_destination_line(classification: Dict[str, Any]) -> Optional[str]:
    """Human-readable destination line for execution plan previews."""
    contract = classification.get("plan_contract") or {}
    dest_side = contract.get("destination") if isinstance(contract, dict) else None
    if isinstance(dest_side, dict):
        from services.planning.schemas import PlanEntityRef

        display = PlanEntityRef.model_validate(dest_side).execution_display()
        if display:
            return f"**Destination:** {display}"

    pd = classification.get("plan_destination") or {}
    name = (classification.get("destination_name") or pd.get("display_name") or "").strip()
    if not name:
        return None
    tab = (classification.get("destination_sheet_name") or pd.get("sheet_name") or "").strip()
    connector = (classification.get("destination") or pd.get("connector_type") or "").strip()
    label = name
    if tab:
        label = f"{name} — tab **{tab}**"
    if connector and connector not in ("unknown",):
        pretty = connector.replace("_", " ")
        return f"**Destination:** {label} ({pretty})"
    return f"**Destination:** {label}"


def format_plan_source_line(classification: Dict[str, Any]) -> Optional[str]:
    """Human-readable source connector + catalog entity for execution plan previews."""
    contract = classification.get("plan_contract") or {}
    src_side = contract.get("source") if isinstance(contract, dict) else None
    if isinstance(src_side, dict):
        from services.planning.schemas import PlanEntityRef

        display = PlanEntityRef.model_validate(src_side).execution_display()
        if display:
            return f"**Source:** {display}"
    name = (classification.get("source_name") or "").strip()
    connector = (classification.get("source") or "").strip().replace("_", " ")
    if not name:
        return None
    if connector and connector not in ("unknown",):
        return f"**Source:** {name} ({connector})"
    return f"**Source:** {name}"


def format_plan_mapping_line(classification: Dict[str, Any]) -> Optional[str]:
    """Human-readable mapping target for execution plan previews."""
    contract = classification.get("plan_contract") or {}
    if not isinstance(contract, dict):
        return None
    mapping = contract.get("mapping_target") or contract.get("destination")
    if not isinstance(mapping, dict):
        return None
    from services.planning.schemas import PlanEntityRef

    ref = PlanEntityRef.model_validate(mapping)
    display = ref.execution_display()
    if not display:
        return None
    return f"**Mapping target:** {display}"


def build_planner_chat_metadata(
    classification: Dict[str, Any],
    *,
    event_type: str,
    planner_pending: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Standard metadata blob for persisted planner messages."""
    meta: Dict[str, Any] = {
        "event_type": event_type,
        "pipeline_state": classification.get("pipeline_state"),
        "plan_id": classification.get("plan_id"),
        "plan_status": classification.get("plan_status"),
    }
    if classification.get("clarifications"):
        meta["clarifications"] = classification.get("clarifications")
    if classification.get("plan_steps_summary"):
        meta["plan_steps_summary"] = classification.get("plan_steps_summary")
    if classification.get("plan_destination"):
        meta["plan_destination"] = classification.get("plan_destination")
    if classification.get("plan_contract"):
        meta["plan_contract"] = classification.get("plan_contract")
    if classification.get("goal_summary"):
        meta["goal_summary"] = classification.get("goal_summary")
    if classification.get("destination_name"):
        meta["destination_name"] = classification.get("destination_name")
    if classification.get("destination_sheet_name"):
        meta["destination_sheet_name"] = classification.get("destination_sheet_name")
    if planner_pending:
        meta["planner_pending"] = planner_pending
    if extra:
        meta.update(extra)
    from services.planning.schemas import json_safe_value

    return json_safe_value({k: v for k, v in meta.items() if v is not None})


def plan_execution_checkpoint(
    *,
    plan_id: Optional[str],
    step_index: int,
    tool_id: str,
    status: str,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "event_type": "plan_step_checkpoint",
        "plan_id": plan_id,
        "step_index": step_index,
        "tool_id": tool_id,
        "status": status,
        "detail": detail or {},
    }
