"""Shared JSON serialization for assessment API and SSE payloads."""
from __future__ import annotations

import json
from typing import Any, Dict
from uuid import UUID

_JSON_JOB_FIELDS = ("connector_ref", "entity_list", "options", "progress", "checkpoint")


def _json_scalar(val: Any) -> Any:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    return val


def parse_json_field(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


def serialize_job(row: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: _json_scalar(v) for k, v in dict(row).items()}
    for key in _JSON_JOB_FIELDS:
        if key in out and out[key] is not None:
            out[key] = parse_json_field(out[key])
    if out.get("report_id") is not None:
        out["report_id"] = str(out["report_id"])
    if out.get("id") is not None:
        out["id"] = str(out["id"])
    return out
