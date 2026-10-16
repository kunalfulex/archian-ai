"""
Endpoint signals and session-derived same-endpoint policy.

Connector instance names are resolved by PLAN_SCOPE + catalog LLM on the planning path.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.planning.resource_catalog import ResourceCatalog
from services.planning.schemas import (
    ConnectorType,
    EndpointResolution,
    EndpointSignals,
    ResolvedEndpoint,
)


def _endpoint(
    connector_type: str,
    display_name: str,
    *,
    sheet_name: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> ResolvedEndpoint:
    ct: ConnectorType = connector_type  # type: ignore[assignment]
    if ct == "gsheets":
        ct = "google_sheets"
    return ResolvedEndpoint(
        connector_type=ct,
        display_name=display_name,
        sheet_name=sheet_name,
        spreadsheet_id=spreadsheet_id,
        connector_id=connector_id,
    )


def resolve_endpoints(
    prompt: Optional[str],
    catalog: ResourceCatalog,
    *,
    classification: Optional[Dict[str, Any]] = None,
) -> EndpointResolution:
    """
    Read endpoint signals and pinned file uploads from classification.

    Connector names are resolved by PLAN_SCOPE + catalog LLM on the planning path.
    """
    del catalog
    signals_raw = merge_endpoint_signals(prompt, classification)
    signals = EndpointSignals(**signals_raw)
    cls = classification or {}

    file_src = None
    from services.file_extraction.pending_upload import is_pinned_file_upload
    from services.planning.connector_guards import canonicalize_connector

    pinned_file = is_pinned_file_upload(cls) or (
        canonicalize_connector(cls.get("source")) == "file"
        and bool((cls.get("source_name") or "").strip())
        and cls.get("_source_endpoint_clarified")
    )
    if pinned_file:
        file_src = _endpoint("file", (cls.get("source_name") or "upload").strip())
    return EndpointResolution(
        source=file_src,
        destination_same_as_source=signals.destination_same_as_source,
        in_place_transform=signals.in_place_transform,
        source_phrase=signals.source_phrase,
        destination_phrase=signals.destination_phrase,
    )


def apply_resolution_to_classification(
    classification: Dict[str, Any],
    resolution: EndpointResolution,
) -> Dict[str, Any]:
    """Merge EndpointResolution into classification dict."""
    out = dict(classification or {})

    if resolution.destination_same_as_source:
        out["destination_same_as_source"] = True
    if resolution.in_place_transform:
        out["in_place_transform"] = True
    if resolution.source_phrase:
        out["source_phrase"] = resolution.source_phrase
    if resolution.destination_phrase:
        out["destination_phrase"] = resolution.destination_phrase

    src = resolution.source
    if src:
        out["source"] = src.connector_type
        out["source_name"] = src.display_name
        if src.sheet_name:
            out["source_sheet_name"] = src.sheet_name
        if src.spreadsheet_id:
            out["source_spreadsheet_id"] = src.spreadsheet_id
        if src.connector_id:
            out["source_connector_id"] = src.connector_id

    dest = resolution.destination
    if dest:
        out["destination"] = dest.connector_type
        out["destination_name"] = dest.display_name
        if dest.sheet_name:
            out["destination_sheet_name"] = dest.sheet_name
        if dest.spreadsheet_id:
            out["destination_spreadsheet_id"] = dest.spreadsheet_id
        if dest.connector_id:
            out["destination_connector_id"] = dest.connector_id

    if resolution.ambiguous:
        out["_endpoint_ambiguous"] = [
            item.model_dump() for item in resolution.ambiguous
        ]
        out["needs_clarification"] = True

    out["_endpoints_resolved"] = True
    return out


def _classification_is_continuation(classification: Dict[str, Any]) -> bool:
    """Mechanical continuation signals from S01 / route — not prompt keywords."""
    sa = (classification.get("session_action") or "").strip().lower()
    if sa == "continue_active_job":
        return True
    phase = (classification.get("continuation_phase") or "none").strip().lower()
    if phase in ("map", "migrate", "data_ops"):
        return True
    if (classification.get("plan_route") or "").strip() == "continuation_path":
        return True
    return False


def _classification_is_compound_pipeline(classification: Dict[str, Any]) -> bool:
    """True when authoritative scope lists extract plus downstream map/migrate."""
    from services.planning.plan_scope import read_plan_scope

    scope = read_plan_scope(classification)
    ops = [
        str(o).lower()
        for o in (scope.get("operations") or classification.get("operations") or [])
    ]
    return "extract" in ops and ("migrate" in ops or "map" in ops)



def extract_endpoint_signals_from_classification(
    classification: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Read optional LLM-provided endpoint relationship fields."""
    c = classification or {}
    return {
        "destination_same_as_source": bool(c.get("destination_same_as_source")),
        "in_place_transform": bool(c.get("in_place_transform")),
        "source_phrase": (c.get("source_phrase") or "").strip() or None,
        "destination_phrase": (c.get("destination_phrase") or "").strip() or None,
    }


def merge_endpoint_signals(
    prompt: Optional[str],
    classification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Read endpoint relationship fields from classification / PLAN_SCOPE only."""
    del prompt
    return extract_endpoint_signals_from_classification(classification)
