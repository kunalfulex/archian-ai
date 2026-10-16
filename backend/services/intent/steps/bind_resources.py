"""S06 — bind classification connector pins to IntentState (no prompt regex)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.intent.state import ConnectorRef, IntentState
from services.intent.types import DestinationNeed, IntentStepId, StepOutcome, StepResult
from services.planning.connector_guards import canonicalize_connector
from services.planning.resource_catalog import ResourceCatalog


def _connector_ref_from_classification(
    cls: Dict[str, Any],
    *,
    role: str,
) -> Optional[ConnectorRef]:
    prefix = "source" if role == "source" else "destination"
    raw_type = cls.get(prefix)
    if not raw_type:
        return None
    ct = canonicalize_connector(str(raw_type))
    if ct in ("unknown", ""):
        return None
    name = (cls.get(f"{prefix}_name") or "").strip()
    connector_id = cls.get(f"{prefix}_connector_id")
    sheet_name = cls.get(f"{prefix}_sheet_name")
    spreadsheet_id = cls.get(f"{prefix}_spreadsheet_id")
    resolved = bool(name or connector_id or spreadsheet_id)
    return ConnectorRef(
        connector_type=ct,
        display_name=name,
        connector_id=connector_id,
        sheet_name=sheet_name,
        spreadsheet_id=spreadsheet_id,
        resolved=resolved,
    )


async def run_step_bind_resources(
    state: IntentState,
    *,
    catalog: ResourceCatalog,
    classification: Optional[Dict[str, Any]] = None,
) -> StepResult:
    del catalog
    cls = dict(classification or state.classifier_snapshot or {})

    from services.file_extraction.pending_upload import is_pinned_file_upload

    if is_pinned_file_upload(cls):
        fname = (cls.get("source_name") or "upload").strip()
        state.source = ConnectorRef(
            connector_type="file",
            display_name=fname,
            resolved=bool(fname),
        )
        state.source_type = "file"
    else:
        src = _connector_ref_from_classification(cls, role="source")
        if src:
            state.source = src
            state.source_type = src.connector_type

    if cls.get("destination_same_as_source"):
        state.destination_same_as_source = True

    needs_dest = state.needs_destination or DestinationNeed.NONE.value
    if needs_dest != DestinationNeed.NONE.value:
        dest = _connector_ref_from_classification(cls, role="destination")
        if dest:
            state.destination = dest
            state.destination_type = dest.connector_type

    state.mark_step(IntentStepId.S06_BIND_RESOURCES.value)
    return StepResult(step_id=IntentStepId.S06_BIND_RESOURCES, outcome=StepOutcome.COMPLETE)
