"""Shared human labels for operation preview + SSE (single source of truth)."""

from __future__ import annotations

from typing import Dict

_OPERATION_LABELS: Dict[str, str] = {
    "transform": "Transform columns",
    "transformation": "Transform columns",
    "filter": "Filter rows",
    "dedupe": "Remove duplicates",
    "deduplication": "Remove duplicates",
    "validate": "Validate & clean values",
    "validation": "Validate & clean values",
    "cleanup_values": "Clean values",
    "cleanup": "Clean up data",
    "create": "Create spreadsheet",
    "join": "Combine sources",
    "upsert": "Upsert rows",
    "search": "Search spreadsheet",
    "update": "Update spreadsheet metadata",
    "deploy_schema": "Deploy metadata",
    "deploy": "Deploy metadata",
    "load_flow": "Load saved flow",
    "save_flow": "Save as flow",
}


def canonical_operation_id(operation_id: str) -> str:
    key = (operation_id or "").strip().lower()
    if key == "transformation":
        return "transform"
    if key == "deduplication":
        return "dedupe"
    if key == "validation":
        return "validate"
    return key


def data_ops_phase_id(operation_id: str) -> str:
    canon = canonical_operation_id(operation_id)
    return f"data_ops_{canon}" if canon else "data_ops_operation"


def label_for_operation(operation_id: str, *, fallback: str | None = None) -> str:
    canon = canonical_operation_id(operation_id)
    if fallback and str(fallback).strip():
        return str(fallback).strip()
    if canon in _OPERATION_LABELS:
        return _OPERATION_LABELS[canon]
    slug = canon.replace("_", " ").strip()
    return slug.title() if slug else "Processing data"


def pipeline_step_for_operation(
    operation_id: str, *, label: str | None = None
) -> dict[str, str]:
    canon = canonical_operation_id(operation_id)
    text = label_for_operation(canon, fallback=label)
    if canon in {"transform", "filter", "dedupe", "validate"}:
        return {
            "id": data_ops_phase_id(canon),
            "label": text,
            "operation_id": canon,
        }
    return {
        "id": canon or "operation",
        "label": text,
        "operation_id": canon,
    }
