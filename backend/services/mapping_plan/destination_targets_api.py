"""List mapping destination targets (objects / tables / sheet tabs) from synced catalogs."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from utils.destination_connector_resolve import (
    resolve_salesforce_token_record,
    resolve_sql_connector_record,
)


def _normalize_dest(destination: str) -> str:
    d = (destination or "").strip().lower()
    return "google_sheets" if d == "gsheets" else d


async def list_destination_targets(
    *,
    user_id: Any,
    destination: str,
    destination_name: str,
) -> Dict[str, Any]:
    """
    Return selectable targets inside a connection for manual destination pickers.

    targets: [{ value, label }] — value is api_name / table_name / sheet_name
    """
    dest = _normalize_dest(destination)
    name = (destination_name or "").strip()
    if not name:
        return {
            "destination": dest,
            "destination_name": name,
            "target_kind": None,
            "target_label": None,
            "targets": [],
        }

    if dest == "salesforce":
        from services.mapping_plan.catalog_target_resolver import (
            _load_salesforce_org_objects,
        )

        tr = await resolve_salesforce_token_record(user_id, destination_name=name)
        org_id = tr.get("org_id") if tr else None
        rows = await _load_salesforce_org_objects(user_id, org_id)
        targets = [
            {
                "value": str(r.get("object_api_name") or ""),
                "label": str(r.get("label") or r.get("object_api_name") or ""),
            }
            for r in rows
            if r.get("object_api_name")
        ]
        targets.sort(key=lambda x: (x["label"] or "").lower())
        return {
            "destination": dest,
            "destination_name": name,
            "target_kind": "object",
            "target_label": "Salesforce object",
            "targets": targets,
        }

    if dest == "sql":
        from services.mapping_plan.catalog_target_resolver import (
            _load_sql_connector_tables,
        )

        tr = await resolve_sql_connector_record(user_id, destination_name=name)
        connector_id = str(tr.get("id")) if tr and tr.get("id") else None
        rows = await _load_sql_connector_tables(user_id, connector_id) if connector_id else []
        targets = [
            {"value": str(r.get("table_name") or ""), "label": str(r.get("table_name") or "")}
            for r in rows
            if r.get("table_name")
        ]
        return {
            "destination": dest,
            "destination_name": name,
            "target_kind": "table",
            "target_label": "SQL table",
            "targets": targets,
        }

    if dest == "google_sheets":
        from services.mapping_plan.catalog_target_resolver import (
            _load_google_sheet_tabs,
        )

        rows = await _load_google_sheet_tabs(user_id, spreadsheet_name=name)
        targets = [
            {"value": str(r.get("sheet_name") or ""), "label": str(r.get("sheet_name") or "")}
            for r in rows
            if r.get("sheet_name")
        ]
        return {
            "destination": dest,
            "destination_name": name,
            "target_kind": "sheet",
            "target_label": "Sheet tab",
            "targets": targets,
        }

    return {
        "destination": dest,
        "destination_name": name,
        "target_kind": None,
        "target_label": None,
        "targets": [],
    }
