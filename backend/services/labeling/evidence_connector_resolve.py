"""Resolve connector refs for label evidence capture."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from db.connection import database
from services.connectors.salesforce_ref import enriched_salesforce_connector_ref, resolve_salesforce_token
from utils.destination_connector_resolve import resolve_salesforce_token_record, resolve_sql_connector_record

ConnectorTarget = Tuple[str, Dict[str, str], Optional[str]]


def _normalize_connector_type(raw: Optional[str]) -> Optional[str]:
    ct = (raw or "").strip().lower()
    if ct in ("gsheets", "google_sheet", "google-sheet"):
        return "google_sheets"
    if ct in ("salesforce", "sql", "google_sheets", "file"):
        return ct if ct != "file" else None
    return ct or None


async def resolve_connector_target(
    user_id: str,
    connector_type: Optional[str],
    *,
    display_name: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> Optional[ConnectorTarget]:
    """Return (connector_type, connector_ref, entity_hint) for evidence storage."""
    ct = _normalize_connector_type(connector_type)
    if not ct:
        return None

    if ct == "salesforce":
        token = None
        if connector_id:
            token = await resolve_salesforce_token(user_id, connector_id)
        if not token:
            rec = await resolve_salesforce_token_record(
                user_id, destination_name=display_name, token_id=connector_id
            )
            if rec:
                token = {
                    "connector_id": str(rec.get("id")),
                    "org_id": str(rec.get("org_id")),
                    "display_name": rec.get("org_display_name") or "Salesforce",
                }
        if not token:
            return None
        return ct, enriched_salesforce_connector_ref(token), None

    if ct == "sql":
        rec = await resolve_sql_connector_record(
            user_id, destination_name=display_name, connector_id=connector_id
        )
        if not rec or not rec.get("id"):
            return None
        cid = str(rec["id"])
        return ct, {"connector_id": cid}, None

    if ct == "google_sheets":
        uid = str(user_id)
        row = None
        if connector_id:
            row = await database.fetch_one(
                """
                SELECT id::text AS id, COALESCE(connection_name, google_account_email) AS name
                FROM google_sheets_connections
                WHERE user_id = :user_id AND id::text = :cid
                LIMIT 1
                """,
                {"user_id": uid, "cid": str(connector_id)},
            )
        name = (display_name or "").strip()
        if not row and name and name.lower() != "unknown":
            row = await database.fetch_one(
                """
                SELECT id::text AS id, COALESCE(connection_name, google_account_email) AS name
                FROM google_sheets_connections
                WHERE user_id = :user_id
                  AND LOWER(TRIM(COALESCE(connection_name, google_account_email, ''))) = LOWER(TRIM(:name))
                LIMIT 1
                """,
                {"user_id": uid, "name": name},
            )
        if not row:
            return None
        cid = str(dict(row)["id"])
        return ct, {"connection_id": cid}, None

    return None


def entity_key_from_classification(
    classification: Dict[str, Any],
    connector_type: str,
) -> Optional[str]:
    obj = (classification.get("destination_object_api_name") or "").strip()
    if connector_type == "salesforce" and obj:
        return obj
    table = (classification.get("primary_table") or classification.get("table_name") or "").strip()
    if connector_type == "sql" and table:
        return table
    sid = (classification.get("destination_spreadsheet_id") or classification.get("spreadsheet_id") or "").strip()
    sheet = (classification.get("destination_sheet_name") or classification.get("sheet_name") or "").strip()
    if connector_type == "google_sheets" and sid and sheet:
        return f"{sid}::{sheet}"
    return obj or table or None
