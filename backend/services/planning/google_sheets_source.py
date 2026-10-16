"""Two-level Google Sheets source: account connection + workbook/tab (mirrors destination)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.planning.google_sheets_destination import (
    _workbook_from_catalog_rows,
    is_gsheets_connection_display_name,
    is_gsheets_workbook_display_name,
)
from services.planning.resource_catalog import ResourceCatalog


def _sync_source_google_connection_ids(out: Dict[str, Any], catalog: ResourceCatalog) -> Dict[str, Any]:
    """Keep google_sheets_connection_id and source_connector_id aligned for GS accounts."""
    cls = dict(out or {})
    conn_id = (cls.get("google_sheets_connection_id") or "").strip()
    src_conn = (cls.get("source_connector_id") or "").strip()
    gs_conn_ids = {
        str(c.connection_id)
        for c in (catalog.google_sheets_connections or [])
        if c.connection_id
    }
    if not conn_id and src_conn and src_conn in gs_conn_ids:
        cls["google_sheets_connection_id"] = src_conn
    elif conn_id and not src_conn:
        cls["source_connector_id"] = conn_id
    elif conn_id and src_conn and src_conn not in gs_conn_ids and conn_id in gs_conn_ids:
        cls["source_connector_id"] = conn_id
    return cls


def gsheets_workbook_source_resolved(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> bool:
    """True when extract source workbook is pinned (not account-only)."""
    if (classification.get("source_spreadsheet_id") or "").strip():
        sid = (classification["source_spreadsheet_id"] or "").strip()
        return any(
            (row.spreadsheet_id or "").strip() == sid
            for row in (catalog.google_sheet_rows or [])
        )
    name = (classification.get("source_name") or "").strip()
    if not name:
        return False
    if is_gsheets_connection_display_name(name, catalog):
        return False
    return is_gsheets_workbook_display_name(name, catalog)


def normalize_google_sheets_source_pins(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Keep account connection ids separate from workbook names / spreadsheet ids."""
    out = _sync_source_google_connection_ids(dict(classification or {}), catalog)
    src = (out.get("source") or "").strip().lower()
    if src not in ("google_sheets", "gsheets"):
        return out

    name = (out.get("source_name") or "").strip()
    account_name = (out.get("source_account_name") or "").strip()

    if name and is_gsheets_connection_display_name(name, catalog):
        if not account_name:
            out["source_account_name"] = name
        out.pop("source_name", None)
        if not gsheets_workbook_source_resolved(out, catalog):
            out.pop("planner_source_confirmed", None)
            out.pop("_source_endpoint_clarified", None)
            out.pop("_source_connector_validated", None)

    spreadsheet_id = (out.get("source_spreadsheet_id") or "").strip()
    src_conn = (out.get("source_connector_id") or "").strip()
    if spreadsheet_id and src_conn == spreadsheet_id:
        conn_id = (out.get("google_sheets_connection_id") or "").strip()
        if conn_id:
            out["source_connector_id"] = conn_id
        elif not is_gsheets_workbook_display_name(name, catalog):
            out.pop("source_connector_id", None)

    return out


def split_gsheets_connector_and_workbook_validation_source(
    classification: Dict[str, Any],
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """validate_classification_resources helper for google_sheets source."""
    out = dict(classification or {})
    src = (out.get("source") or "").lower()
    if src not in ("google_sheets", "gsheets"):
        return out
    return normalize_google_sheets_source_pins(out, catalog)


def apply_google_sheets_source_account_pick(
    classification: Dict[str, Any],
    pick: Any,
    catalog: ResourceCatalog,
) -> Dict[str, Any]:
    """Pin Google account connection only — workbook resolved separately."""
    out = dict(classification or {})
    out["source"] = "google_sheets"
    if pick.connector_id:
        cid = str(pick.connector_id)
        out["google_sheets_connection_id"] = cid
        out["source_connector_id"] = cid
    if pick.display_name:
        out["source_account_name"] = str(pick.display_name).strip()
    if (out.get("source_name") or "").strip().lower() == (
        (pick.display_name or "").strip().lower()
    ):
        out.pop("source_name", None)
    out["_source_connector_resolution"] = "user_clarification"
    if getattr(pick, "reasoning_short", None):
        out["_source_connector_reasoning"] = pick.reasoning_short

    out = normalize_google_sheets_source_pins(out, catalog)
    from services.planning.source_catalog_validate import (
        mark_source_validated_from_llm,
        strip_unvalidated_source_confirm_flags,
        validate_source_connector_pins,
    )

    if validate_source_connector_pins(out, catalog):
        return mark_source_validated_from_llm(out, catalog=catalog)
    return strip_unvalidated_source_confirm_flags(out)


def apply_google_sheets_source_workbook_match(
    classification: Dict[str, Any],
    match: Dict[str, Any],
    *,
    source: str = "catalog",
    catalog: Optional[ResourceCatalog] = None,
) -> Dict[str, Any]:
    out = dict(classification or {})
    out["source"] = "google_sheets"
    if match.get("spreadsheet_name"):
        out["source_name"] = str(match["spreadsheet_name"]).strip()
    if match.get("spreadsheet_id"):
        out["source_spreadsheet_id"] = str(match["spreadsheet_id"])
    if match.get("sheet_name"):
        out["source_sheet_name"] = str(match["sheet_name"]).strip()
    conn_id = match.get("google_sheets_connection_id")
    if conn_id:
        out["google_sheets_connection_id"] = str(conn_id)
        out["source_connector_id"] = str(conn_id)
    sid = match.get("spreadsheet_id")
    tab = match.get("sheet_name")
    if sid and tab:
        out["source_object_api_name"] = f"{sid}::{tab}"
        out["source_table_name"] = f"{sid}::{tab}"
    out["_google_sheets_workbook_resolution"] = source
    from services.planning.source_catalog_validate import (
        ensure_google_sheets_source_connection_pin,
        mark_source_validated_from_llm,
        strip_unvalidated_source_confirm_flags,
        validate_source_connector_pins,
    )

    if catalog is not None:
        out = ensure_google_sheets_source_connection_pin(out, catalog)
        out = normalize_google_sheets_source_pins(out, catalog)
        if validate_source_connector_pins(out, catalog):
            return mark_source_validated_from_llm(out, catalog=catalog)
    return strip_unvalidated_source_confirm_flags(out)


async def resolve_and_apply_google_sheets_source_workbook(
    classification: Dict[str, Any],
    *,
    workbook_name: str,
    sheet_name: Optional[str],
    catalog: ResourceCatalog,
    user_id: str,
) -> Dict[str, Any]:
    """Resolve a named source workbook/tab via DB + catalog."""
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
        source="source_llm",
    )
    out = dict(classification or {})

    if db_result.get("found"):
        return apply_google_sheets_source_workbook_match(
            out, db_result, source="source_db", catalog=catalog
        )

    if db_result.get("error") == "ambiguous_spreadsheet_name":
        out["source"] = "google_sheets"
        out["source_name"] = wb
        if sheet_name:
            out["source_sheet_name"] = sheet_name
        out["_google_sheets_workbook_ambiguous"] = True
        out["_google_sheets_workbook_candidates"] = db_result.get("candidates") or []
        return out

    catalog_row = _workbook_from_catalog_rows(wb, sheet_name, catalog)
    if catalog_row and catalog_row.get("spreadsheet_id"):
        from services.google_sheets_connection_service import resolve_workbook_by_spreadsheet_id

        by_id = await resolve_workbook_by_spreadsheet_id(
            str(user_id),
            str(catalog_row["spreadsheet_id"]),
            source="source_catalog",
        )
        if by_id.get("found"):
            return apply_google_sheets_source_workbook_match(
                out, by_id, source="source_catalog", catalog=catalog
            )

    if catalog_row:
        return apply_google_sheets_source_workbook_match(
            out, catalog_row, source="catalog_row_only", catalog=catalog
        )

    return out
