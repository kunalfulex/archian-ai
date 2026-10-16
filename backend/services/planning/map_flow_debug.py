"""Temporary high-signal tracing for map continuation + Run plan (always INFO)."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("edison.map_flow")


def _safe_json(obj: Any, limit: int = 3000) -> str:
    try:
        return json.dumps(obj, default=str)[:limit]
    except Exception:
        return str(obj)[:limit]


def log_map_flow(event: str, **fields: Any) -> None:
    payload = {k: v for k, v in fields.items() if v is not None}
    logger.info("[map_flow] event=%s %s", event, _safe_json(payload))


def classification_map_pins(classification: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    cls = classification or {}
    ep = cls.get("execution_plan") if isinstance(cls.get("execution_plan"), dict) else {}
    steps = ep.get("steps") or []
    return {
        "plan_route": cls.get("plan_route"),
        "continuation_steps": cls.get("_continuation_steps") or cls.get("_pipeline_tokens"),
        "plan_status": cls.get("plan_status"),
        "intent": cls.get("intent"),
        "mode": cls.get("mode"),
        "active_job_id": cls.get("active_job_id"),
        "source": cls.get("source"),
        "source_name": cls.get("source_name"),
        "source_object_api_name": cls.get("source_object_api_name"),
        "source_entity_key": cls.get("source_entity_key"),
        "destination": cls.get("destination"),
        "destination_name": cls.get("destination_name"),
        "destination_account_name": cls.get("destination_account_name"),
        "destination_object_api_name": cls.get("destination_object_api_name"),
        "destination_spreadsheet_id": cls.get("destination_spreadsheet_id"),
        "destination_connector_id": cls.get("destination_connector_id"),
        "google_sheets_connection_id": cls.get("google_sheets_connection_id"),
        "planner_destination_entity_confirmed": cls.get("planner_destination_entity_confirmed"),
        "needs_clarification": cls.get("needs_clarification"),
        "plan_id": cls.get("plan_id"),
        "step_count": len(steps),
        "step_tools": [
            (s.get("parameters") or {}).get("tool_id") or s.get("step_type")
            for s in steps
            if isinstance(s, dict)
        ][:5],
    }
