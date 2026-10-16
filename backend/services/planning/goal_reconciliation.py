"""Reconcile PLAN_SCOPE hypotheses with catalog/binding ground truth (no prompt keywords)."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from services.planning.schemas import PlanContract, normalize_connector_type


def _goal_has_create_hypothesis(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
) -> bool:
    goal = dict(parsed_goal or {})
    ops = [str(o).lower() for o in (goal.get("operations") or [])]
    return bool(
        goal.get("create_destination")
        or "create" in ops
        or classification.get("planner_create_spreadsheet")
    )


def catalog_proved_existing_gsheet_destination(
    classification: Dict[str, Any],
    plan_contract: Optional[PlanContract],
) -> bool:
    """
    True when binding or classification pins a real workbook (spreadsheet_id), not a new title.
    """
    ct = normalize_connector_type(classification.get("destination") or "")
    if ct != "google_sheets":
        return False

    sid = (classification.get("destination_spreadsheet_id") or "").strip()
    dest = plan_contract.destination if plan_contract else None
    if not sid and dest:
        sid = (dest.spreadsheet_id or "").strip()
        if not sid and dest.entity_key and "::" in dest.entity_key:
            sid = dest.entity_key.split("::", 1)[0].strip()

    if not sid:
        return False

    if dest and dest.is_resolved():
        return True
    if (classification.get("destination_sheet_name") or "").strip():
        return True
    if (classification.get("destination_name") or "").strip():
        return True
    return False


def reconcile_goal_after_catalog_binding(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
    plan_contract: Optional[PlanContract],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """
    When catalog binding proved an existing Google Sheet destination, clear a stale
    PLAN_SCOPE ``create`` hypothesis (same state as ``pick_workbook`` selecting existing).

    Returns (classification, parsed_goal, changed).
    """
    out = dict(classification or {})
    goal = dict(parsed_goal or {})

    if not _goal_has_create_hypothesis(out, goal):
        return out, goal, False
    if not catalog_proved_existing_gsheet_destination(out, plan_contract):
        return out, goal, False
    from services.planning.destination_catalog_validate import (
        destination_connector_authoritatively_confirmed,
    )

    # While create is active, only user workbook picker (not LLM catalog bind) may drop create.
    if out.get("planner_create_spreadsheet") or goal.get("create_destination"):
        if not out.get("planner_workbook_picker_done"):
            return out, goal, False
    elif not (
        destination_connector_authoritatively_confirmed(out)
        or out.get("planner_workbook_picker_done")
    ):
        return out, goal, False

    ops = [str(o).lower() for o in (goal.get("operations") or out.get("operations") or [])]
    if "create" in ops:
        ops = [o for o in ops if o != "create"]
        goal["operations"] = ops
        out["operations"] = ops

    goal["create_destination"] = False
    out["planner_create_spreadsheet"] = False
    out.pop("planner_new_spreadsheet_name", None)
    out["planner_destination_confirmed"] = True

    dest = plan_contract.destination if plan_contract else None
    if dest and dest.is_resolved():
        out["planner_destination_entity_confirmed"] = True
        if dest.sheet_name:
            out["destination_sheet_name"] = dest.sheet_name
        if dest.spreadsheet_id:
            out["destination_spreadsheet_id"] = dest.spreadsheet_id
        if dest.display_name and not (out.get("destination_name") or "").strip():
            out["destination_name"] = dest.display_name

    from services.planning.plan_scope import (
        read_plan_scope,
        resolve_pipeline_tokens_from_goal,
        sync_scope_into_classification,
        write_plan_scope,
    )

    out = write_plan_scope(out, goal)
    tokens = resolve_pipeline_tokens_from_goal(out, goal)
    out = sync_scope_into_classification(out, goal, tokens)

    return out, read_plan_scope(out) or goal, True


def drop_stale_create_for_pre_pinned_workbook(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """
    When a workbook was already pinned (binding/validated) but PLAN_SCOPE still lists create,
    drop the stale create hypothesis and keep the existing destination.
    """
    out = dict(classification or {})
    goal = dict(parsed_goal or {})

    if not _goal_has_create_hypothesis(out, goal):
        return out, goal, False
    if not catalog_proved_existing_gsheet_destination(out, None):
        return out, goal, False
    if not (out.get("destination_spreadsheet_id") or "").strip():
        return out, goal, False

    ops = [str(o).lower() for o in (goal.get("operations") or out.get("operations") or [])]
    if "create" in ops:
        ops = [o for o in ops if o != "create"]
        goal["operations"] = ops
        out["operations"] = ops

    goal["create_destination"] = False
    out["planner_create_spreadsheet"] = False
    out.pop("planner_new_spreadsheet_name", None)
    out["planner_destination_confirmed"] = True

    from services.planning.plan_scope import (
        read_plan_scope,
        resolve_pipeline_tokens_from_goal,
        sync_scope_into_classification,
        write_plan_scope,
    )

    out = write_plan_scope(out, goal)
    tokens = resolve_pipeline_tokens_from_goal(out, goal)
    out = sync_scope_into_classification(out, goal, tokens)

    return out, read_plan_scope(out) or goal, True


def reconcile_existing_destination_flags(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
    plan_contract: Optional[PlanContract] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """Clear stale create flags when an existing Google Sheet destination is pinned."""
    return reconcile_goal_after_catalog_binding(classification, parsed_goal, plan_contract)
