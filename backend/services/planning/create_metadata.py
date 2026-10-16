"""Create-spreadsheet metadata from PLAN_SCOPE LLM (+ regex fallback when LLM omits fields)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _goal_has_create(goal: Dict[str, Any]) -> bool:
    ops = [str(o).lower() for o in (goal.get("operations") or [])]
    return bool(goal.get("create_destination")) or "create" in ops


def classification_requests_new_spreadsheet(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when PLAN_SCOPE requires a new Google Sheet (not picking an existing workbook).

    Stays true until the user explicitly picks an existing workbook from the catalog picker.
    """
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if classification.get("planner_workbook_picker_done"):
        return False
    if _goal_has_create(goal) or classification.get("planner_create_spreadsheet"):
        return True
    return False


def clear_catalog_workbook_pins_for_create(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Remove catalog workbook pins while create is active — keep Google account pins only.

    Prevents LLM/legacy hints from binding an existing sheet when the user asked to create new.
    """
    from services.planning.plan_scope import read_plan_scope

    if not classification_requests_new_spreadsheet(classification, parsed_goal):
        return dict(classification or {})

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    out = dict(classification or {})

    if spreadsheet_title_slot_satisfied(out, goal):
        return out

    from services.planning.destination_catalog_validate import (
        strip_unvalidated_destination_confirm_flags,
    )

    out = strip_unvalidated_destination_confirm_flags(out)
    for key in (
        "destination_spreadsheet_id",
        "destination_sheet_name",
        "destination_object_api_name",
        "_google_sheets_workbook_resolution",
        "_google_sheets_workbook_ambiguous",
        "_google_sheets_workbook_candidates",
    ):
        out.pop(key, None)

    title = (out.get("planner_new_spreadsheet_name") or "").strip()
    dest_name = (out.get("destination_name") or "").strip()
    if dest_name and dest_name != title:
        out.pop("destination_name", None)

    for key in (
        "planner_destination_confirmed",
        "planner_destination_entity_confirmed",
        "_destination_endpoint_clarified",
        "_destination_connector_validated",
        "_destination_connector_resolution",
    ):
        out.pop(key, None)

    if not (out.get("destination") or "").strip():
        out["destination"] = "google_sheets"
    return out


_GENERIC_SPREADSHEET_TITLES = frozenset(
    {
        "google sheet",
        "google sheets",
        "new spreadsheet",
        "new sheet",
        "spreadsheet",
        "sheet",
        "workbook",
        "new workbook",
        "gsheet",
        "g sheet",
    }
)


def is_generic_spreadsheet_title(title: Optional[str]) -> bool:
    """True when title is a connector label or placeholder, not a user-chosen name."""
    t = (title or "").strip().lower()
    if not t:
        return True
    if t in _GENERIC_SPREADSHEET_TITLES:
        return True
    if t.startswith("google sheet"):
        return True
    return False


def resolved_spreadsheet_title(classification: Dict[str, Any], goal: Optional[Dict[str, Any]] = None) -> str:
    """User-confirmed or goal title when non-generic."""
    g = dict(goal or {})
    for candidate in (
        (classification.get("planner_new_spreadsheet_name") or "").strip(),
        (g.get("spreadsheet_title") or "").strip(),
        (classification.get("planner_suggested_spreadsheet_title") or "").strip(),
    ):
        if candidate and not is_generic_spreadsheet_title(candidate):
            return candidate
    return ""


def spreadsheet_title_slot_satisfied(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """CREATE_TITLE slot: requires explicit user confirmation with a non-generic title."""
    if not classification.get("planner_spreadsheet_title_confirmed"):
        return False
    title = (classification.get("planner_new_spreadsheet_name") or "").strip()
    if not title:
        title = (classification.get("destination_name") or "").strip()
    if not title or is_generic_spreadsheet_title(title):
        return False
    return True


def sync_create_assembly_readiness(
    classification: Dict[str, Any],
    goal: Optional[Dict[str, Any]] = None,
    *,
    plan: Any = None,
) -> Dict[str, Any]:
    """
    Align create metadata before plan step materialization.

    Promotes confirmed titles from slot_fills / destination_name onto planner fields so
    op_requirements and build_plan_steps agree (avoids empty_plan_no_steps).
    """
    from services.planning.slot_fills import sync_authoritative_flags_from_slot_fills

    out = sync_authoritative_flags_from_slot_fills(dict(classification or {}), plan=plan)
    g = dict(goal or {})

    if not (_goal_has_create(g) or out.get("planner_create_spreadsheet")):
        return out

    title = resolved_spreadsheet_title(out, g)
    if out.get("planner_spreadsheet_title_confirmed") and title:
        if not (out.get("planner_new_spreadsheet_name") or "").strip():
            out["planner_new_spreadsheet_name"] = title
        if not (out.get("destination_name") or "").strip():
            out["destination_name"] = title
        out.setdefault("planner_create_spreadsheet", True)
    return out


def _normalize_headers(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        s = str(item or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def resolve_create_pipeline_tokens(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
    pipeline_tokens: Optional[List[str]] = None,
) -> List[str]:
    """Ordered pipeline tokens for create/header policy (classification → goal → operations)."""
    from services.planning.plan_scope import read_plan_scope, resolve_pipeline_tokens_from_goal

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if pipeline_tokens:
        tokens = [str(t).lower() for t in pipeline_tokens if str(t).strip()]
    else:
        tokens = classification.get("_pipeline_tokens")
        if isinstance(tokens, list) and tokens:
            tokens = [str(t).lower() for t in tokens if str(t).strip()]
        elif goal:
            tokens = resolve_pipeline_tokens_from_goal(classification, goal)
        else:
            tokens = [
                str(o).lower() for o in (classification.get("operations") or []) if str(o).strip()
            ]

    goal_ops = [str(o).lower() for o in (goal.get("operations") or []) if str(o).strip()]
    if "extract" in goal_ops and "extract" not in tokens:
        from services.planning.pipeline_step_resolver import _order_tokens

        merged = list(tokens)
        merged.insert(0, "extract")
        tokens = _order_tokens(merged)
    return tokens


from services.planning.source_column_headers import (  # noqa: E402 — re-export for callers
    create_headers_from_extraction,
    create_headers_slot_satisfied,
    create_headers_use_extraction,
    filter_create_header_clarifications,
    read_spreadsheet_header_source,
    should_require_create_headers_clarification,
)


def apply_create_metadata_to_classification(
    classification: Dict[str, Any],
    parsed_goal: Dict[str, Any],
) -> Dict[str, Any]:
    """Project PLAN_SCOPE create metadata onto classification for plan_builder."""
    out = dict(classification or {})
    had_workbook_pin = bool((out.get("destination_spreadsheet_id") or "").strip())
    goal = dict(parsed_goal or {})
    from services.planning.goal_reconciliation import catalog_proved_existing_gsheet_destination

    if catalog_proved_existing_gsheet_destination(out, None):
        if had_workbook_pin:
            from services.planning.goal_reconciliation import drop_stale_create_for_pre_pinned_workbook

            out, goal, _ = drop_stale_create_for_pre_pinned_workbook(out, goal)
            return out
        if out.get("planner_workbook_picker_done"):
            from services.planning.goal_reconciliation import reconcile_existing_destination_flags

            out, goal, _ = reconcile_existing_destination_flags(out, goal, None)
            return out
    if not _goal_has_create(goal):
        return out

    out["planner_create_spreadsheet"] = True
    goal["create_destination"] = True

    title = (goal.get("spreadsheet_title") or "").strip()
    if title:
        if out.get("planner_spreadsheet_title_confirmed") and not is_generic_spreadsheet_title(title):
            out["planner_new_spreadsheet_name"] = title
            out["destination_name"] = title
            out["destination_object_api_name"] = title
            out["planner_destination_confirmed"] = True
            out["planner_destination_entity_confirmed"] = True
            out.pop("destination_spreadsheet_id", None)
            out.pop("destination_sheet_name", None)
        else:
            out["planner_suggested_spreadsheet_title"] = title

    dest = str(out.get("destination") or "").lower()
    if not dest or dest in ("unknown", ""):
        out["destination"] = "google_sheets"

    from services.planning.source_column_headers import (
        HEADER_SOURCE_FROM_EXTRACTION,
        read_spreadsheet_header_source,
    )

    headers = _normalize_headers(goal.get("spreadsheet_headers"))
    header_source = read_spreadsheet_header_source(out, goal)
    if headers and header_source != HEADER_SOURCE_FROM_EXTRACTION:
        out["planner_spreadsheet_headers"] = headers
        goal["spreadsheet_headers"] = headers
    elif header_source == HEADER_SOURCE_FROM_EXTRACTION:
        out.pop("planner_spreadsheet_headers", None)

    return out


def goal_explicitly_creates_spreadsheet(goal: Optional[Dict[str, Any]]) -> bool:
    """True when parsed goal includes a create destination (no prompt keywords)."""
    return _goal_has_create(dict(goal or {}))


def should_materialize_create_step(
    classification: Dict[str, Any],
    goal: Dict[str, Any],
) -> bool:
    """False when user confirmed an existing workbook or route blocks create."""
    if not (_goal_has_create(goal) or classification.get("planner_create_spreadsheet")):
        return False
    if (
        classification.get("planner_create_spreadsheet")
        and not classification.get("planner_workbook_picker_done")
    ):
        if spreadsheet_title_slot_satisfied(classification, goal):
            return True
        if not resolved_spreadsheet_title(classification, goal):
            return False
    if classification.get("planner_destination_confirmed") and not classification.get(
        "planner_create_spreadsheet"
    ):
        return False
    # Catalog/binding proof of an existing workbook overrides a stale PLAN_SCOPE create flag.
    if (classification.get("destination_spreadsheet_id") or "").strip():
        return False
    from services.planning.connector_guards import blocks_spreadsheet_create_for_route

    return not blocks_spreadsheet_create_for_route(classification, goal, None)


def sync_create_export_operations(
    goal: Dict[str, Any],
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Validate PLAN_SCOPE ops against connector state.

    Previously appended create/map/migrate mechanically — that logic is removed;
    pipeline_ops come from PLAN_SCOPE LLM only.
    """
    from services.planning.scope_validator import validate_plan_scope_goal

    return validate_plan_scope_goal(dict(goal or {}), classification)


def sync_export_pipeline_operations(
    goal: Dict[str, Any],
    classification: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate PLAN_SCOPE goal (alias kept for callers during migration)."""
    return sync_create_export_operations(goal, classification)
