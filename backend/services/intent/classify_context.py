"""Shared resource catalog load for classifier + intent pipeline."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog, load_resource_catalog


@dataclass
class ClassifyContext:
    """User connector snapshot for one classify / intent turn."""

    catalog: ResourceCatalog
    sql_connection_names: List[str] = field(default_factory=list)
    sql_connections_display: List[str] = field(default_factory=list)
    salesforce_connections_json: Dict[str, Any] = field(default_factory=lambda: {"connections": []})
    salesforce_connections_formatted: List[str] = field(default_factory=list)
    has_salesforce: bool = False
    google_sheet_rows: List[Dict[str, Any]] = field(default_factory=list)
    google_sheets_list: List[str] = field(default_factory=list)
    has_google_sheets: bool = False


async def load_classify_context(user_id: str) -> ClassifyContext:
    """Load catalog once; derive display strings for the classifier prompt."""
    catalog = await load_resource_catalog(user_id)
    ctx = ClassifyContext(catalog=catalog)

    for conn in catalog.sql_connections:
        name = (conn.name or "").strip()
        if not name:
            continue
        ctx.sql_connection_names.append(name)
        ctx.sql_connections_display.append(f"{name} ({conn.connector_type or 'sql'})")

    sf_list: List[Dict[str, Any]] = []
    for conn in catalog.salesforce_connections:
        label = (conn.name or "Salesforce").strip()
        env = conn.environment_type or "unknown"
        sf_list.append({"name": label, "environment": env, "status": "Connected"})
        ctx.salesforce_connections_formatted.append(f"{label} ({env}) - Connected")
    ctx.salesforce_connections_json = {"connections": sf_list}
    ctx.has_salesforce = bool(sf_list)

    ctx.has_google_sheets = catalog.has_google_sheets
    seen: set = set()
    for row in catalog.google_sheet_rows:
        sn = (row.spreadsheet_name or "").strip()
        tab = (row.sheet_name or "").strip() or "Sheet1"
        key = (sn.lower(), tab.lower())
        if not sn or key in seen:
            continue
        seen.add(key)
        ctx.google_sheet_rows.append(
            {
                "spreadsheet_name": sn,
                "sheet_name": tab,
                "spreadsheet_id": row.spreadsheet_id,
            }
        )
        ctx.google_sheets_list.append(f'workbook "{sn}"' + (f" (tab: {tab})" if tab else ""))

    return ctx


def build_catalog_from_classify_fetch(
    *,
    sql_connection_names: List[str],
    salesforce_connections_json: Dict[str, Any],
    google_sheet_rows: List[Dict[str, Any]],
    has_google_sheets: bool,
) -> ResourceCatalog:
    """Build catalog from legacy classify fetch kwargs (no extra DB round-trip)."""
    from services.planning.resource_catalog import (
        GoogleSheetRef,
        ResourceCatalog,
        SalesforceConnectionRef,
        SqlConnectionRef,
    )

    catalog = ResourceCatalog(has_google_sheets=has_google_sheets)
    for name in sql_connection_names:
        if name:
            catalog.sql_connections.append(SqlConnectionRef(name=str(name).strip()))
    for conn in salesforce_connections_json.get("connections") or []:
        if conn.get("name"):
            catalog.salesforce_connections.append(
                SalesforceConnectionRef(
                    name=conn["name"],
                    environment_type=conn.get("environment"),
                )
            )
    for row in google_sheet_rows or []:
        sn = (row.get("spreadsheet_name") or "").strip()
        if not sn:
            continue
        catalog.google_sheet_rows.append(
            GoogleSheetRef(
                spreadsheet_name=sn,
                sheet_name=(row.get("sheet_name") or "").strip() or "Sheet1",
                spreadsheet_id=row.get("spreadsheet_id"),
            )
        )
    return catalog


def build_conversation_context_for_classification(
    conversation_history: Optional[List[Dict]],
    current_prompt: str,
    *,
    fresh_extract: bool = False,
    max_user_turns: int = 4,
) -> str:
    """
    Build classifier context from user turns only.

    Assistant/data_preview messages carry stale source/destination wording and must
    not override the current user message.
    """
    if fresh_extract:
        return "(prior turns omitted — classify from Current Message only)"
    if not conversation_history:
        return "None"

    current_norm = (current_prompt or "").strip()
    user_lines: List[str] = []
    for msg in conversation_history:
        role = (msg.get("type") or msg.get("role") or "").lower()
        if role not in ("user",):
            continue
        text = (msg.get("text") or "").strip()
        if not text or text == current_norm:
            continue
        user_lines.append(text)

    if not user_lines:
        return "None"
    tail = user_lines[-max_user_turns:]
    return "\n".join(f"user: {line}" for line in tail)


def continuation_classifier_context() -> str:
    """Context string when classifying a post-extract continuation turn."""
    return (
        "(prior user turns omitted — Active State holds the latest extraction. "
        "Classify intent from the Current Message in natural language: map_data, "
        "migrate_data, transform/filter, etc. Copy source from Active State; set "
        "destination_name when the user names a target org/workbook/DB. Set "
        "destination_object_api_name when they name a Salesforce object e.g. Contact, "
        "Account, Lead — including typos like 'oject' for object.)"
    )
