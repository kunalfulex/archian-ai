"""Connector-symmetric read/write of pinned source/destination entities."""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from services.planning.schemas import PlanEntityRef, normalize_connector_type

SideRole = Literal["source", "destination"]


def pinned_entity_key(
    classification: Dict[str, Any],
    *,
    role: SideRole,
    connector_type: Optional[str] = None,
) -> str:
    """Return the user- or planner-pinned entity key for one side (SF/SQL/GSheets)."""
    ct = normalize_connector_type(
        connector_type
        or classification.get("source" if role == "source" else "destination")
        or ""
    )
    if role == "source":
        if ct == "salesforce":
            return (classification.get("source_object_api_name") or "").strip()
        if ct == "sql":
            return (
                (classification.get("source_table_name") or classification.get("table_name") or "")
                .strip()
            )
        if ct == "google_sheets":
            return (
                (classification.get("source_sheet_name") or classification.get("sheet_name") or "")
                .strip()
            )
    else:
        if ct == "salesforce":
            return (
                (classification.get("destination_object_api_name") or classification.get("object_api_name") or "")
                .strip()
            )
        if ct == "sql":
            return (
                (
                    classification.get("destination_table_name")
                    or classification.get("destination_table")
                    or ""
                ).strip()
            )
        if ct == "google_sheets":
            return (classification.get("destination_sheet_name") or "").strip()
    return ""


def write_pinned_entity_key(
    classification: Dict[str, Any],
    *,
    role: SideRole,
    connector_type: str,
    entity_key: str,
) -> Dict[str, Any]:
    """Set classification fields for a pinned entity on one side."""
    out = dict(classification or {})
    key = (entity_key or "").strip()
    if not key:
        return out
    ct = normalize_connector_type(connector_type)
    if role == "source":
        if ct == "salesforce":
            out["source_object_api_name"] = key
        elif ct == "sql":
            out["source_table_name"] = key
        elif ct == "google_sheets":
            out["source_sheet_name"] = key
    else:
        if ct == "salesforce":
            out["destination_object_api_name"] = key
            out["object_api_name"] = key
        elif ct == "sql":
            out["destination_table_name"] = key
        elif ct == "google_sheets":
            out["destination_sheet_name"] = key
    return out


def destination_entity_confirmed(classification: Dict[str, Any]) -> bool:
    return bool(classification.get("planner_destination_entity_confirmed"))


def source_entity_confirmed(classification: Dict[str, Any]) -> bool:
    return bool(classification.get("planner_source_entity_confirmed"))


def plan_entity_ref_from_pin(
    classification: Dict[str, Any],
    *,
    role: SideRole,
    entity_key: str,
    label: Optional[str] = None,
    resolution_source: str = "user_explicit",
    confidence: float = 0.95,
) -> PlanEntityRef:
    """Build a resolved PlanEntityRef from a pinned entity key."""
    prefix = "source" if role == "source" else "destination"
    ct = normalize_connector_type(classification.get(prefix) or "")
    key = (entity_key or "").strip()
    display = (classification.get(f"{prefix}_name") or "").strip() or None
    cid = (classification.get(f"{prefix}_connector_id") or "").strip() or None
    sheet_name = None
    spreadsheet_id = None
    if ct == "google_sheets":
        spreadsheet_id = (
            (classification.get(f"{prefix}_spreadsheet_id") or "").strip() or None
        )
        if "::" in key:
            sid, tab = key.split("::", 1)
            spreadsheet_id = spreadsheet_id or sid
            sheet_name = tab
        else:
            sheet_name = key
    lbl = (label or key).strip() or key
    return PlanEntityRef(
        connector_type=ct,
        connector_id=cid,
        display_name=display,
        entity_key=key or None,
        catalog_label=lbl,
        entity_label=lbl,
        sheet_name=sheet_name,
        spreadsheet_id=spreadsheet_id,
        resolution_source=resolution_source,  # type: ignore[arg-type]
        confidence=confidence,
    )


def pin_destination_from_classification(
    classification: Dict[str, Any],
) -> Optional[PlanEntityRef]:
    """When the user confirmed destination entity, return a resolved ref (any connector)."""
    if not destination_entity_confirmed(classification):
        return None
    key = pinned_entity_key(classification, role="destination")
    if not key:
        return None
    return plan_entity_ref_from_pin(classification, role="destination", entity_key=key)
