"""Approve a draft execution plan and prepare classification for routing."""
from __future__ import annotations

from typing import Any, Dict

from services.planning.schemas import merge_plan_into_classification
from services.planning.schemas import ExecutionPlan
from services.planning.plan_context import pipeline_state_from_plan


def approve_execution_plan(pending: Dict[str, Any]) -> Dict[str, Any]:
    """Mark plan approved and merge into classification dict."""
    base = dict(pending.get("classification") or {})
    plan_data = pending.get("execution_plan") or base.get("execution_plan") or {}
    plan = ExecutionPlan.model_validate(plan_data)
    from services.planning.planning_checkpoint import hydrate_classification_from_checkpoint

    # Restore staged file ref / connector pins when frontend pending classification is thin.
    base = hydrate_classification_from_checkpoint(base, plan)
    plan.status = "approved"
    out = merge_plan_into_classification(base, plan)
    out["needs_clarification"] = False
    out["plan_status"] = "approved"
    out["pipeline_state"] = pipeline_state_from_plan("approved")

    _PRESERVE_KEYS = (
        "operations",
        "data_operations",
        "active_job_id",
        "_plan_completed_step_ids",
        "plan_resume_step_index",
        "mapping_id",
        "mapping_config",
        "plan_route",
        "original_prompt",
        "planning_artifact",
        "source",
        "destination",
        "source_name",
        "destination_name",
        "destination_sheet_name",
        "destination_object_api_name",
        "destination_table_name",
        "source_object_api_name",
        "source_table_name",
        "source_sheet_name",
        "plan_contract",
        "intent",
        "mode",
        "planner_create_spreadsheet",
        "planner_new_spreadsheet_name",
        "_file_upload_pinned",
        "_pending_file_ref",
        "destination_connector_id",
        "source_connector_id",
    )
    for key in _PRESERVE_KEYS:
        if base.get(key) is not None:
            out[key] = base[key]

    from services.planning.map_flow_debug import classification_map_pins, log_map_flow

    log_map_flow(
        "plan_approved",
        plan_id=out.get("plan_id"),
        classification_pins=classification_map_pins(out),
        pending_had_execution_plan=bool(pending.get("execution_plan")),
        original_prompt_excerpt=(pending.get("original_prompt") or "")[:200],
    )

    from services.planning.goal_reconciliation import reconcile_existing_destination_flags
    from services.planning.plan_scope import read_plan_scope
    from services.planning.schemas import PlanContract

    parsed_goal = read_plan_scope(out)
    if not parsed_goal and isinstance(plan.metadata, dict):
        raw_goal = plan.metadata.get("goal")
        parsed_goal = dict(raw_goal) if isinstance(raw_goal, dict) else {}
    plan_contract = None
    if isinstance(plan.metadata, dict) and plan.metadata.get("plan_contract"):
        try:
            plan_contract = PlanContract.model_validate(plan.metadata["plan_contract"])
        except Exception:
            plan_contract = None
    out, _, _ = reconcile_existing_destination_flags(out, parsed_goal or {}, plan_contract)

    return out
