"""Destination connector validation — catalog membership only (no keyword routing).

LLM or user clarification proposes pins; this module checks they exist in the user's catalog.
Planning must not treat destination as resolved until validation passes.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import normalize_connector_type


def read_needs_destination(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """True when PLAN_SCOPE / pipeline requires destination connector details."""
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if goal.get("needs_destination") is False:
        return False
    if goal.get("needs_destination") is True:
        return True

    from services.planning.op_requirements import (
        derive_plan_requirements,
        read_plan_requirements,
        requirements_need_destination_connector,
    )

    reqs = read_plan_requirements(classification, parsed_goal=goal or None)
    if requirements_need_destination_connector(reqs):
        return True

    ops = [str(o).lower() for o in (goal.get("operations") or classification.get("operations") or [])]
    if ops:
        tokens = classification.get("_pipeline_tokens") or ops
        derived = derive_plan_requirements(
            list(tokens),
            classification=classification,
            parsed_goal=goal,
        )
        return requirements_need_destination_connector(derived)

    from services.planning.connector_guards import (
        classification_needs_destination_endpoint,
        is_unknown_resource_name,
    )

    dest_phrase = (
        goal.get("destination_phrase") or classification.get("destination_phrase") or ""
    ).strip()
    if (
        dest_phrase
        and is_unknown_resource_name(classification.get("destination_name"))
        and classification_needs_destination_endpoint(classification)
    ):
        return True
    return False


def _sf_org_in_catalog(name: str, catalog: ResourceCatalog) -> bool:
    needle = (name or "").strip().lower()
    return bool(needle) and any((n or "").strip().lower() == needle for n in catalog.salesforce_names)


def _sql_connection_in_catalog(name: str, catalog: ResourceCatalog) -> bool:
    needle = (name or "").strip().lower()
    return bool(needle) and any((n or "").strip().lower() == needle for n in catalog.sql_names)


def google_sheets_account_slot_satisfied(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when google_sheets_connection_id is pinned and in catalog."""
    conns = catalog.google_sheets_connections or []
    if not conns:
        return True
    conn_id = (classification.get("google_sheets_connection_id") or "").strip()
    if not conn_id:
        return False
    return _gs_account_in_catalog(conn_id, catalog)


def ensure_google_sheets_connection_pin(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """
    Pin the sole Google account when unambiguous.

    Only for post-LLM-apply / post-user-pick catalog validation — not gap detection.
    """
    out = dict(classification or {})
    if (out.get("google_sheets_connection_id") or "").strip():
        return out
    conns = [c for c in (catalog.google_sheets_connections or []) if c.connection_id]
    if len(conns) != 1:
        return out
    cid = str(conns[0].connection_id)
    out["google_sheets_connection_id"] = cid
    out["destination_connector_id"] = cid
    if conns[0].name:
        out["destination_account_name"] = conns[0].name
    return out


def pin_implied_google_sheets_account(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """
    When the user has exactly one Google account, pin it only after authoritative validation.

    Not used during gap detection — only after LLM validate or user clarification apply.
    """
    out = dict(classification or {})
    if not destination_connector_authoritatively_confirmed(out):
        return out
    conns = [c for c in (catalog.google_sheets_connections or []) if c.connection_id]
    if len(conns) != 1:
        return out
    if (out.get("google_sheets_connection_id") or "").strip():
        return out
    cid = str(conns[0].connection_id)
    out["google_sheets_connection_id"] = cid
    out["destination_connector_id"] = cid
    if conns[0].name:
        out["destination_account_name"] = conns[0].name
    return out


def needs_google_sheets_account_pick(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when migrate/map Google Sheets destination needs account disambiguation."""
    from services.planning.schemas import normalize_connector_type

    dest = normalize_connector_type(classification.get("destination") or "")
    if dest not in ("google_sheets", "gsheets"):
        return False
    return not google_sheets_account_slot_satisfied(classification, catalog)


def needs_google_sheets_destination_clarification(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    True when PLAN_SCOPE / ops require a Google Sheets destination connector clarification.

    Uses read_needs_destination + catalog slot satisfaction — not prompt regex.
    """
    from services.planning.plan_scope import read_plan_scope

    goal = dict(parsed_goal or read_plan_scope(classification) or {})
    if not read_needs_destination(classification, goal):
        return False

    dest = normalize_connector_type(classification.get("destination") or "")
    if dest not in ("google_sheets", "gsheets"):
        return False

    if classification.get("planner_workbook_picker_done"):
        return False

    if classification.get("planner_create_spreadsheet"):
        from services.planning.create_metadata import spreadsheet_title_slot_satisfied

        if not spreadsheet_title_slot_satisfied(classification, goal):
            return False

    if destination_connector_slot_satisfied(classification, catalog, parsed_goal=goal):
        return False

    return True


def _gs_account_in_catalog(connection_id: str, catalog: ResourceCatalog) -> bool:
    cid = (connection_id or "").strip()
    if not cid:
        return False
    return any(str(c.connection_id) == cid for c in catalog.google_sheets_connections or [])


def _gs_workbook_in_catalog(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    sid = (classification.get("destination_spreadsheet_id") or "").strip()
    if sid:
        for row in catalog.google_sheet_rows or []:
            if (row.spreadsheet_id or "").strip() == sid:
                return True
        return False

    name = (classification.get("destination_name") or "").strip()
    if not name:
        return False
    from services.planning.google_sheets_destination import is_gsheets_workbook_display_name

    return is_gsheets_workbook_display_name(name, catalog)


def validate_destination_connector_pins(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """
    True when destination connector instance pins reference real catalog rows.

    Google Sheets: validated account (when required) + workbook/create path.
    Salesforce / SQL: validated org or connection id/name.
    """
    dest = normalize_connector_type(classification.get("destination") or "")
    if dest in ("unknown", "", "file"):
        return False

    if dest == "google_sheets":
        cls = ensure_google_sheets_connection_pin(classification, catalog)
        if catalog.google_sheets_connections and not google_sheets_account_slot_satisfied(
            cls, catalog
        ):
            return False

        if cls.get("planner_create_spreadsheet"):
            if cls.get("planner_spreadsheet_title_confirmed") and (
                cls.get("planner_new_spreadsheet_name") or ""
            ).strip():
                return True
            return False

        # Connector layer: Google account only; workbook/tab resolved at entity bind.
        return True

    if dest == "salesforce":
        token = (classification.get("destination_connector_id") or "").strip()
        if token:
            return any(str(c.token_id) == token for c in catalog.salesforce_connections or [])
        name = (classification.get("destination_name") or "").strip()
        return _sf_org_in_catalog(name, catalog)

    if dest == "sql":
        cid = (classification.get("destination_connector_id") or "").strip()
        if cid:
            return any(str(c.connector_id) == cid for c in catalog.sql_connections or [])
        name = (classification.get("destination_name") or "").strip()
        return _sql_connection_in_catalog(name, catalog)

    return False


def destination_connector_authoritatively_confirmed(
    classification: Dict[str, Any],
) -> bool:
    """True when pins came from user clarification, validated LLM, or job inherit."""
    if classification.get("_destination_endpoint_clarified"):
        return True
    if classification.get("_destination_connector_validated"):
        return True
    if classification.get("_destination_inherited_from_job"):
        return True
    from services.planning.slot_fills import slot_fills_destination_connector_confirmed

    if slot_fills_destination_connector_confirmed(classification):
        return True
    return False


def destination_connector_slot_satisfied(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
    *,
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Destination connector requirement satisfied: needs_destination off, or validated authoritative pins.
    """
    if not read_needs_destination(classification, parsed_goal):
        return True
    dest = normalize_connector_type(classification.get("destination") or "")
    if dest in ("unknown", ""):
        return False
    if not destination_connector_authoritatively_confirmed(classification):
        return False
    return validate_destination_connector_pins(classification, catalog)


def strip_unvalidated_destination_confirm_flags(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Remove destination confirm flags when pins are not authoritatively validated."""
    from services.planning.slot_fills import sync_authoritative_flags_from_slot_fills

    out = dict(classification or {})
    if destination_connector_authoritatively_confirmed(out):
        return sync_authoritative_flags_from_slot_fills(out)
    for key in (
        "planner_destination_confirmed",
        "planner_destination_entity_confirmed",
        "_destination_endpoint_clarified",
        "_destination_connector_validated",
    ):
        out.pop(key, None)
    return out


def clear_destination_pins_for_revalidation(classification: Dict[str, Any]) -> Dict[str, Any]:
    """Drop destination instance pins so LLM/clarification can re-resolve."""
    out = dict(classification or {})
    for key in (
        "destination_name",
        "destination_connector_id",
        "destination_spreadsheet_id",
        "destination_sheet_name",
        "destination_account_name",
        "google_sheets_connection_id",
        "destination_object_api_name",
        "planner_destination_confirmed",
        "planner_destination_entity_confirmed",
        "_destination_endpoint_clarified",
        "_destination_connector_validated",
        "_destination_connector_resolution",
    ):
        out.pop(key, None)
    return out


def clear_destination_when_not_needed(
    classification: Dict[str, Any],
    parsed_goal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Remove destination pins when PLAN_SCOPE says needs_destination is false."""
    if read_needs_destination(classification, parsed_goal):
        return dict(classification or {})
    out = dict(classification or {})
    for key in (
        "destination",
        "destination_name",
        "destination_connector_id",
        "destination_spreadsheet_id",
        "destination_sheet_name",
        "destination_account_name",
        "google_sheets_connection_id",
        "destination_object_api_name",
        "destination_phrase",
        "planner_destination_confirmed",
        "planner_destination_entity_confirmed",
        "planner_create_spreadsheet",
        "_destination_endpoint_clarified",
        "_destination_connector_validated",
    ):
        out.pop(key, None)
    out["needs_destination"] = False
    return out


def mark_destination_validated_from_llm(
    classification: Dict[str, Any],
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    """Call after LLM pick passes catalog validation."""
    out = dict(classification or {})
    out["_destination_connector_validated"] = True
    out["_destination_connector_resolution"] = "llm"
    out["_destination_endpoint_clarified"] = True
    if catalog is not None:
        out = pin_implied_google_sheets_account(out, catalog)

    dest = normalize_connector_type(out.get("destination") or "")
    if dest == "google_sheets" and catalog is not None:
        from services.planning.create_metadata import classification_requests_new_spreadsheet
        from services.planning.google_sheets_destination import gsheets_workbook_destination_resolved
        from services.planning.plan_scope import read_plan_scope

        goal = read_plan_scope(out)
        if not gsheets_workbook_destination_resolved(out, catalog) and not (
            classification_requests_new_spreadsheet(out, goal)
            and (out.get("planner_new_spreadsheet_name") or "").strip()
        ):
            return out

    out["planner_destination_confirmed"] = True
    return out
