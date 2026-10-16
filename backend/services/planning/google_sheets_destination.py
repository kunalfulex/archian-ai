"""Two-level Google Sheets destination: account connection + workbook/tab."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from services.planning.resource_catalog import ResourceCatalog


def gsheets_connection_names(catalog: ResourceCatalog) -> List[str]:
    return [
        (c.name or "").strip()
        for c in (catalog.google_sheets_connections or [])
        if (c.name or "").strip()
    ]


def is_gsheets_connection_display_name(name: Optional[str], catalog: ResourceCatalog) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return any(x.strip().lower() == n for x in gsheets_connection_names(catalog))


def is_gsheets_workbook_display_name(name: Optional[str], catalog: ResourceCatalog) -> bool:
    n = (name or "").strip().lower()
    if not n:
        return False
    return any(x.strip().lower() == n for x in catalog.spreadsheet_names)


def gsheets_workbook_destination_resolved(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when migrate/map target workbook is pinned (not account-only)."""
    if (classification or {}).get("planner_create_spreadsheet"):
        return True
    if (classification.get("destination_spreadsheet_id") or "").strip():
        return True
    name = (classification.get("destination_name") or "").strip()
    if not name:
        return False
    if is_gsheets_connection_display_name(name, catalog):
        return False
    return is_gsheets_workbook_display_name(name, catalog)


def normalize_google_sheets_destination_pins(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """
    Keep account connection ids separate from workbook names / spreadsheet ids.
    """
    out = dict(classification or {})
    dest = (out.get("destination") or "").strip().lower()
    if dest not in ("google_sheets", "gsheets"):
        return out

    conn_id = (out.get("google_sheets_connection_id") or "").strip()
    dest_conn = (out.get("destination_connector_id") or "").strip()
    if not conn_id and dest_conn:
        if any(
            str(c.connection_id) == dest_conn
            for c in (catalog.google_sheets_connections or [])
            if c.connection_id
        ):
            out["google_sheets_connection_id"] = dest_conn

    name = (out.get("destination_name") or "").strip()
    account_name = (out.get("destination_account_name") or "").strip()

    if name and is_gsheets_connection_display_name(name, catalog):
        if not account_name:
            out["destination_account_name"] = name
        out.pop("destination_name", None)
        if not gsheets_workbook_destination_resolved(out, catalog):
            out.pop("planner_destination_confirmed", None)
            out.pop("_destination_endpoint_clarified", None)

    spreadsheet_id = (out.get("destination_spreadsheet_id") or "").strip()
    if spreadsheet_id and dest_conn == spreadsheet_id:
        # destination_connector_id must hold account id, not spreadsheet id
        if conn_id:
            out["destination_connector_id"] = conn_id
        elif not is_gsheets_workbook_display_name(name, catalog):
            out.pop("destination_connector_id", None)

    return out


def workbook_name_hint_from_destination_phrase(
    phrase: str,
    *,
    account_display_name: Optional[str] = None,
    catalog: Optional[ResourceCatalog] = None,
) -> Optional[str]:
    """
    Extract workbook label from PLAN_SCOPE destination_phrase (mechanical strip).

    Prefer catalog spreadsheet name substring match; otherwise strip account/workbook suffixes.
    """
    raw = (phrase or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if catalog:
        for name in sorted(catalog.spreadsheet_names, key=len, reverse=True):
            nl = (name or "").strip().lower()
            if nl and nl in low:
                return name.strip()
    p = raw
    if account_display_name:
        acc = account_display_name.strip()
        p = re.sub(
            rf"(?i)\s+of\s+{re.escape(acc)}\s*(?:google\s+account)?\s*$",
            "",
            p,
        ).strip()
        p = re.sub(
            rf"(?i)\s+{re.escape(acc)}\s*(?:google\s+account)?\s*$",
            "",
            p,
        ).strip()
    for pattern in (
        r"(?i)\s+workbook\s*$",
        r"(?i)\s+spreadsheet\s*$",
        r"(?i)\s+sheet\s*$",
        r"(?i)\s+google\s+account\s*$",
    ):
        p = re.sub(pattern, "", p).strip()
    return p.strip() or None


def apply_google_sheets_account_pick(
    classification: Dict[str, Any],
    pick: Any,
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Pin Google account connection only — workbook resolved separately."""
    out = dict(classification or {})
    out["destination"] = "google_sheets"
    if pick.connector_id:
        cid = str(pick.connector_id)
        out["google_sheets_connection_id"] = cid
        out["destination_connector_id"] = cid
    if pick.display_name:
        out["destination_account_name"] = str(pick.display_name).strip()
    if (out.get("destination_name") or "").strip().lower() == (
        (pick.display_name or "").strip().lower()
    ):
        out.pop("destination_name", None)
    out["_destination_connector_resolution"] = "user_clarification"
    if getattr(pick, "reasoning_short", None):
        out["_destination_connector_reasoning"] = pick.reasoning_short

    out = normalize_google_sheets_destination_pins(out, catalog)
    from services.planning.destination_catalog_validate import (
        mark_destination_validated_from_llm,
        validate_destination_connector_pins,
    )

    if validate_destination_connector_pins(out, catalog):
        return mark_destination_validated_from_llm(out, catalog=catalog)
    out["_destination_endpoint_clarified"] = True
    return out


def apply_google_sheets_workbook_match(
    classification: Dict[str, Any],
    match: Dict[str, Any],
    *,
    source: str = "catalog",
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    out = dict(classification or {})
    from services.planning.create_metadata import (
        classification_requests_new_spreadsheet,
        clear_catalog_workbook_pins_for_create,
    )
    from services.planning.plan_scope import read_plan_scope

    goal = read_plan_scope(out)
    if classification_requests_new_spreadsheet(out, goal):
        return clear_catalog_workbook_pins_for_create(out, goal)

    out["destination"] = "google_sheets"
    if match.get("spreadsheet_name"):
        out["destination_name"] = str(match["spreadsheet_name"]).strip()
    if match.get("spreadsheet_id"):
        out["destination_spreadsheet_id"] = str(match["spreadsheet_id"])
    if match.get("sheet_name"):
        out["destination_sheet_name"] = str(match["sheet_name"]).strip()
    conn_id = match.get("google_sheets_connection_id")
    if conn_id:
        out["google_sheets_connection_id"] = str(conn_id)
        out["destination_connector_id"] = str(conn_id)
    sid = match.get("spreadsheet_id")
    tab = match.get("sheet_name")
    if sid and tab:
        out["destination_object_api_name"] = f"{sid}::{tab}"
    out["_google_sheets_workbook_resolution"] = source
    from services.planning.destination_catalog_validate import (
        ensure_google_sheets_connection_pin,
        mark_destination_validated_from_llm,
        strip_unvalidated_destination_confirm_flags,
        validate_destination_connector_pins,
    )

    out = ensure_google_sheets_connection_pin(out, catalog) if catalog is not None else out
    if catalog is not None and validate_destination_connector_pins(out, catalog):
        return mark_destination_validated_from_llm(out, catalog=catalog)
    return strip_unvalidated_destination_confirm_flags(out)


def _source_workbook_for_resolution(
    job: Any,
    classification: Dict[str, Any],
) -> Optional[str]:
    src = getattr(getattr(job, "source", None), "display_name", None)
    if src and str(src).strip():
        return str(src).strip()
    return (classification.get("source_name") or "").strip() or None


def _workbook_from_catalog_rows(
    workbook_name: str,
    sheet_name: Optional[str],
    catalog: ResourceCatalog,
) -> Optional[Dict[str, Any]]:
    needle = (workbook_name or "").strip().lower()
    if not needle:
        return None
    tab_needle = (sheet_name or "").strip().lower() if sheet_name else None
    for row in catalog.google_sheet_rows or []:
        sn = (row.spreadsheet_name or "").strip()
        if sn.lower() != needle:
            continue
        tab = (row.sheet_name or "Sheet1").strip()
        if tab_needle and tab.lower() != tab_needle:
            continue
        return {
            "spreadsheet_id": row.spreadsheet_id,
            "spreadsheet_name": sn,
            "sheet_name": tab,
        }
    return None


def pin_google_sheets_destination_after_create(
    classification: Dict[str, Any],
    *,
    spreadsheet_id: str,
    sheet_name: str,
    spreadsheet_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Pin tab + spreadsheet id on classification after google_sheets_create_from_prompt.

    Enables map_fields has_pinned_entity without a separate entity clarification.
    """
    from services.planning.continuation_policy import (
        sync_plan_contract_destination_from_classification,
    )

    out = dict(classification or {})
    tab = (sheet_name or "Sheet1").strip() or "Sheet1"
    sid = (spreadsheet_id or "").strip()
    title = (spreadsheet_name or out.get("destination_name") or "").strip()

    out["destination"] = out.get("destination") or "google_sheets"
    out["destination_sheet_name"] = tab
    if sid:
        out["destination_spreadsheet_id"] = sid
    if title:
        out["destination_name"] = title
    if sid and tab:
        out["destination_object_api_name"] = f"{sid}::{tab}"
    out["planner_destination_entity_confirmed"] = True
    return sync_plan_contract_destination_from_classification(
        out,
        resolution_source="plan_pinned",
        confidence=0.95,
    )


async def resolve_and_apply_google_sheets_workbook(
    classification: Dict[str, Any],
    *,
    workbook_name: str,
    sheet_name: Optional[str],
    catalog: ResourceCatalog,
    user_id: str,
) -> Dict[str, Any]:
    """Resolve a named workbook/tab via DB + catalog (no prompt keyword matching)."""
    wb = (workbook_name or "").strip()
    if not wb:
        return dict(classification or {})
    if not catalog.google_sheet_rows and not catalog.has_google_sheets:
        return dict(classification or {})

    from services.google_sheets_connection_service import resolve_workbook_by_name

    db_result = await resolve_workbook_by_name(
        str(user_id),
        wb,
        sheet_name,
        source="continuation_llm",
    )
    out = dict(classification or {})

    if db_result.get("found"):
        return apply_google_sheets_workbook_match(
            out, db_result, source="continuation_db", catalog=catalog
        )

    if db_result.get("error") == "ambiguous_spreadsheet_name":
        out["destination"] = "google_sheets"
        out["destination_name"] = wb
        if sheet_name:
            out["destination_sheet_name"] = sheet_name
        out["_google_sheets_workbook_ambiguous"] = True
        out["_google_sheets_workbook_candidates"] = db_result.get("candidates") or []
        return out

    catalog_row = _workbook_from_catalog_rows(wb, sheet_name, catalog)
    if catalog_row and catalog_row.get("spreadsheet_id"):
        from services.google_sheets_connection_service import resolve_workbook_by_spreadsheet_id

        by_id = await resolve_workbook_by_spreadsheet_id(
            str(user_id),
            str(catalog_row["spreadsheet_id"]),
            source="continuation_catalog",
        )
        if by_id.get("found"):
            return apply_google_sheets_workbook_match(
                out, by_id, source="continuation_catalog", catalog=catalog
            )

    if catalog_row:
        return apply_google_sheets_workbook_match(
            out, catalog_row, source="catalog_row_only", catalog=catalog
        )

    return out


def split_gsheets_connector_and_workbook_validation(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """validate_classification_resources helper for google_sheets destination."""
    out = dict(classification or {})
    dest = (out.get("destination") or "").lower()
    if dest not in ("google_sheets", "gsheets"):
        return out
    return normalize_google_sheets_destination_pins(out, catalog)
