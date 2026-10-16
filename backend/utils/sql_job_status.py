"""Build GET /job-status response for SQL extraction jobs (sql_tasks Redis store)."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import HTTPException

from utils.extraction_sse_stream import sql_task_to_job_status_payload


async def build_sql_job_status_response(job_id: str, user_id: UUID, sql_tasks) -> Dict[str, Any]:
    if not await sql_tasks.exists(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    task = await sql_tasks.get(job_id, {}) or {}
    task_user_id = task.get("user_id")
    if str(task_user_id) != str(user_id):
        raise HTTPException(status_code=403, detail="Access denied")

    sanitized = dict(sql_task_to_job_status_payload(job_id, task))
    sanitized.pop("connector_data", None)
    sanitized.pop("comprehensive_prompt", None)

    connector_name = None
    try:
        if task.get("connector_data") and task["connector_data"].get("connector"):
            connector = task["connector_data"]["connector"]
            connector_name = (
                dict(connector).get("name") if hasattr(connector, "_mapping") else connector.get("name")
            )
    except Exception:
        connector_name = None
    if connector_name:
        sanitized["connector_name"] = connector_name

    return json.loads(json.dumps(sanitized, default=str))
