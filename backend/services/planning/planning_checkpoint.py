"""Durable planning checkpoint: scope + connector pins across clarification resumes."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from services.planning.plan_scope import read_plan_scope, write_plan_scope
from services.planning.schemas import ExecutionPlan

# Pins restored from checkpoint when pending classification is thin.
_CHECKPOINT_PIN_KEYS = (
    "source",
    "source_name",
    "source_connector_id",
    "source_spreadsheet_id",
    "source_sheet_name",
    "source_table_name",
    "source_object_api_name",
    "destination",
    "destination_name",
    "destination_connector_id",
    "destination_spreadsheet_id",
    "destination_sheet_name",
    "destination_object_api_name",
    "google_sheets_connection_id",
    "operations",
    "_source_endpoint_clarified",
    "_destination_endpoint_clarified",
    "_source_connector_validated",
    "_destination_connector_validated",
    "_file_upload_pinned",
    # S3 key for chat-attached file bytes across clarify / Run plan (no raw bytes in SSE).
    "_pending_file_ref",
    "planner_source_confirmed",
    "planner_destination_confirmed",
    "planner_workbook_picker_done",
    "planner_source_workbook_picker_done",
    "planner_destination_entity_confirmed",
    "planner_source_entity_confirmed",
)


def plan_scope_is_resolved(goal: Optional[Dict[str, Any]]) -> bool:
    """True when PLAN_SCOPE is safe to use for clear_* / assembly gates."""
    g = dict(goal or {})
    if g.get("operations") or g.get("data_operations"):
        return True
    if g.get("needs_source") is not None or g.get("needs_destination") is not None:
        return True
    return False


def pin_value_present(value: Any) -> bool:
    """True when a checkpoint pin value is meaningfully set (string, list, or bool)."""
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict)):
        return bool(value)
    return True


def should_restore_pin(prior_val: Any, merged_val: Any) -> bool:
    if not pin_value_present(prior_val):
        return False
    if not pin_value_present(merged_val):
        return True
    if isinstance(prior_val, str) and isinstance(merged_val, str):
        return bool(prior_val.strip()) and not bool(merged_val.strip())
    return False


def _classification_checkpoint_slice(classification: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    scope = read_plan_scope(classification)
    if scope:
        out["_plan_scope"] = scope
    for key in _CHECKPOINT_PIN_KEYS:
        val = classification.get(key)
        if pin_value_present(val):
            out[key] = val
    slot_fills = classification.get("_slot_fills")
    if isinstance(slot_fills, dict) and slot_fills:
        out["_slot_fills"] = dict(slot_fills)
    return out


def _merge_checkpoint_pins(
    classification: Dict[str, Any],
    checkpoint: Dict[str, Any],
) -> Dict[str, Any]:
    """Fill missing pins from a prior checkpoint without overwriting fresh apply state."""
    out = dict(classification or {})
    ck = dict(checkpoint or {})
    scope = ck.get("_plan_scope")
    if isinstance(scope, dict) and scope and not read_plan_scope(out):
        out = write_plan_scope(out, scope)
        if scope.get("operations") and not out.get("operations"):
            out["operations"] = scope["operations"]
    for key in _CHECKPOINT_PIN_KEYS:
        if should_restore_pin(ck.get(key), out.get(key)):
            out[key] = ck[key]
    prior_fills = ck.get("_slot_fills")
    if isinstance(prior_fills, dict) and prior_fills:
        merged_fills = {**prior_fills, **(out.get("_slot_fills") or {})}
        out["_slot_fills"] = merged_fills
    return out


def enrich_google_sheets_bind_pins(
    classification: Dict[str, Any],
    *,
    catalog: Any = None,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    After LLM/catalog bind, normalize GS connector pins so slot checks pass.

    Copies spreadsheet_id from entity keys (sid::tab), mirrors destination_connector_id
    to google_sheets_connection_id, and marks workbook picker done when slot is satisfied.
    """
    from services.planning.resource_catalog import coerce_planning_catalog
    from services.planning.schemas import normalize_connector_type

    out = dict(classification or {})
    goal = dict(parsed_goal or read_plan_scope(out) or {})

    for obj_key, sid_key, sheet_key in (
        ("source_object_api_name", "source_spreadsheet_id", "source_sheet_name"),
        ("destination_object_api_name", "destination_spreadsheet_id", "destination_sheet_name"),
    ):
        entity = (out.get(obj_key) or "").strip()
        if "::" not in entity:
            continue
        sid, tab = entity.split("::", 1)
        if sid.strip() and not (out.get(sid_key) or "").strip():
            out[sid_key] = sid.strip()
        if tab.strip() and not (out.get(sheet_key) or "").strip():
            out[sheet_key] = tab.strip()

    table = (out.get("source_table_name") or "").strip()
    if "::" in table and not (out.get("source_spreadsheet_id") or "").strip():
        sid, tab = table.split("::", 1)
        if sid.strip():
            out["source_spreadsheet_id"] = sid.strip()
        if tab.strip() and not (out.get("source_sheet_name") or "").strip():
            out["source_sheet_name"] = tab.strip()

    catalog_ref = coerce_planning_catalog(catalog or out.get("_planning_catalog"), out)
    if catalog_ref is not None:
        from services.planning.google_sheets_destination import normalize_google_sheets_destination_pins
        from services.planning.destination_catalog_validate import destination_connector_slot_satisfied

        out = normalize_google_sheets_destination_pins(out, catalog_ref)
        if normalize_connector_type(out.get("destination") or "") in ("google_sheets", "gsheets"):
            if destination_connector_slot_satisfied(out, catalog_ref, parsed_goal=goal):
                out["planner_workbook_picker_done"] = True

    return out


def persist_planning_checkpoint(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], ExecutionPlan]:
    """
    Write scope and connector pins to plan metadata before clarifications or pending emit.

    Single checkpoint used by all clarification types on metadata resume.
    """
    from services.planning.slot_fills import (
        mirror_slot_fills_to_classification,
        record_authoritative_connector_slot_fills,
        write_slot_fills_to_plan,
    )

    out = dict(classification or {})
    goal = dict(parsed_goal or read_plan_scope(out) or {})
    plan = plan.model_copy(deep=True)
    plan.metadata = dict(plan.metadata or {})

    if goal:
        out = write_plan_scope(out, goal)
        if goal.get("operations"):
            out["operations"] = goal["operations"]
        plan.metadata["goal"] = goal

    out = enrich_google_sheets_bind_pins(
        out,
        catalog=out.get("_planning_catalog"),
        parsed_goal=goal,
    )

    fills = record_authoritative_connector_slot_fills(out, plan=plan)
    plan = write_slot_fills_to_plan(plan, fills)
    out = mirror_slot_fills_to_classification(out, plan)

    plan.metadata["classification_checkpoint"] = _classification_checkpoint_slice(out)
    return out, plan


def hydrate_classification_from_checkpoint(
    classification: Dict[str, Any],
    plan: ExecutionPlan,
) -> Dict[str, Any]:
    """Restore scope and pins from plan checkpoint before clarification apply/resume."""
    from services.planning.slot_fills import mirror_slot_fills_to_classification

    out = dict(classification or {})
    meta = plan.metadata if isinstance(plan.metadata, dict) else {}

    goal = read_plan_scope(out)
    if not plan_scope_is_resolved(goal) and isinstance(meta.get("goal"), dict):
        meta_goal = dict(meta["goal"])
        if plan_scope_is_resolved(meta_goal):
            out = write_plan_scope(out, meta_goal)
            if meta_goal.get("operations"):
                out["operations"] = meta_goal["operations"]

    checkpoint = meta.get("classification_checkpoint")
    if isinstance(checkpoint, dict):
        out = _merge_checkpoint_pins(out, checkpoint)

    return mirror_slot_fills_to_classification(out, plan)


def resolved_goal_for_session(
    classification: Dict[str, Any],
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
    plan: Optional[ExecutionPlan] = None,
) -> Dict[str, Any]:
    """Best-effort PLAN_SCOPE for kernel clear_* gates."""
    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if plan_scope_is_resolved(goal):
        return goal
    if plan and isinstance((plan.metadata or {}).get("goal"), dict):
        meta_goal = dict(plan.metadata["goal"])
        if plan_scope_is_resolved(meta_goal):
            return meta_goal
    return goal
