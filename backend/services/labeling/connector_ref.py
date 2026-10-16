"""Normalize connector references across SF / SQL / GSheets."""
from __future__ import annotations

from typing import Any, Dict, Tuple

from services.connectors.salesforce_ref import (
    normalize_salesforce_connector_ref,
    salesforce_public_connector_id,
)


async def normalize_connector_ref(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Dict[str, Any]:
    if connector_type == "salesforce":
        return await normalize_salesforce_connector_ref(user_id, connector_ref)
    return dict(connector_ref or {})


def connector_instance_id(connector_type: str, connector_ref: Dict[str, Any]) -> str:
    if connector_type == "salesforce":
        return salesforce_public_connector_id(connector_ref)
    if connector_type == "sql":
        return str(connector_ref.get("connector_id") or "")
    if connector_type == "google_sheets":
        return str(connector_ref.get("connection_id") or "")
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def connector_ref_from_instance_id(connector_type: str, connector_instance_id: str) -> Dict[str, str]:
    inst = str(connector_instance_id or "").strip()
    if connector_type == "salesforce":
        return {"connector_id": inst}
    if connector_type == "sql":
        return {"connector_id": inst}
    if connector_type == "google_sheets":
        return {"connection_id": inst}
    raise ValueError(f"Unsupported connector_type: {connector_type}")


def parse_gsheet_entity_key(entity_key: str) -> Tuple[str, str]:
    """entity_key format: spreadsheet_id::sheet_name"""
    if "::" not in entity_key:
        raise ValueError(f"Invalid google_sheets entity_key: {entity_key}")
    sid, sheet = entity_key.split("::", 1)
    return sid, sheet
