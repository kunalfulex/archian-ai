"""Map legacy extraction_setup phases to unified operation_phase keys."""
from __future__ import annotations

from typing import Optional, Tuple

# setup phase -> (operation_phase_id, default_label)
_SETUP_PHASE_TO_OPERATION: dict[str, Tuple[str, str]] = {
    "resolve_org": ("connect_source", "Connecting to Salesforce"),
    "resolve_connection": ("connect_source", "Connecting to data source"),
    "read_sheet": ("connect_source", "Reading spreadsheet"),
    "resolve_object": ("inspect_catalog", "Reviewing fields and structure"),
    "resolve_table": ("inspect_catalog", "Reviewing table schema"),
    "build_query": ("build_extraction_plan", "Building extraction plan"),
    "execute": ("extract_records", "Extracting records"),
    "processing": ("extract_records", "Extracting records"),
}


def format_identify_route_label(classification: dict) -> str:
    """Human-readable source → destination line for operation preview."""
    if not classification:
        return "Identifying source and destination"
    source = (classification.get("source") or "unknown").replace("_", " ").title()
    dest = (classification.get("destination") or "unknown").replace("_", " ").title()
    sn = (classification.get("source_name") or "").strip()
    dn = (classification.get("destination_name") or "").strip()
    left = f"{source}" + (f" ({sn})" if sn else "")
    right = f"{dest}" + (f" ({dn})" if dn else "")
    if sn or dn:
        return f"{left} → {right}"
    return "Identifying source and destination"


def operation_from_setup_phase(
    setup_phase: str,
    *,
    message: Optional[str] = None,
    source_type: str = "salesforce",
) -> Tuple[str, str]:
    """Return (operation_phase_id, label)."""
    key = (setup_phase or "").strip().lower()
    if key in _SETUP_PHASE_TO_OPERATION:
        op_id, default = _SETUP_PHASE_TO_OPERATION[key]
        # Keep step labels stable; granular progress text belongs in operation_phase `detail` only.
        return op_id, default
    if source_type == "sql":
        return "extract_records", "Extracting from database"
    if source_type == "google_sheets":
        return "extract_records", "Reading Google Sheet"
    return "extract_records", "Extracting records"
