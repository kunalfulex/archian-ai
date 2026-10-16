"""Normalize connector_ref between Enterprise Graph storage and other agents."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def assessment_connector_ref(connector_type: str, connector_ref: Dict[str, Any]) -> Dict[str, Any]:
    """Connector ref shape expected by data assessment jobs."""
    ct = (connector_type or "").strip().lower()
    ref = dict(connector_ref or {})
    if ct == "salesforce":
        out: Dict[str, Any] = {}
        cid = ref.get("connector_id") or ref.get("token_id")
        org_id = ref.get("org_id")
        if cid:
            out["connector_id"] = str(cid)
        if org_id:
            out["org_id"] = str(org_id)
        return out or {"connector_id": "", "org_id": ""}
    if ct == "sql":
        return {"connector_id": ref.get("connector_id") or ""}
    if ct == "google_sheets":
        cid = ref.get("google_sheets_connection_id") or ref.get("connection_id") or ""
        return {"google_sheets_connection_id": cid, "connection_id": cid}
    return ref


def sheet_entity_allowlist_from_ref(connector_ref: Dict[str, Any]) -> Optional[List[str]]:
    """
    Google Sheets entity allowlist on graph members.
    None = assess all sheets; [] = assess none; [ids] = scoped sheets.
    """
    raw = (connector_ref or {}).get("sheet_entity_allowlist")
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    if len(raw) == 0:
        return []
    out = [str(x).strip() for x in raw if x and str(x).strip()]
    return out if out else None


def connector_ref_for_storage(connector_type: str, connector_id: str) -> Dict[str, str]:
    """Persisted ref on enterprise_graph_members (includes Sheets key used by DAA)."""
    ct = (connector_type or "").strip().lower()
    cid = (connector_id or "").strip()
    if ct == "salesforce":
        return {"connector_id": cid}
    if ct == "sql":
        return {"connector_id": cid}
    if ct == "google_sheets":
        return {"google_sheets_connection_id": cid, "connection_id": cid}
    raise ValueError(f"Unsupported connector_type: {connector_type}")
