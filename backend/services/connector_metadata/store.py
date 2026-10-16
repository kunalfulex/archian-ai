"""Persistence for connector metadata jobs, schedules, and graphs."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional

from db.connection import database


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


async def create_job(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    sync_mode: str,
    scope: str,
    options: Dict[str, Any],
) -> str:
    job_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO connector_metadata_jobs (
            id, user_id, connector_type, connector_ref, sync_mode, scope, options,
            status, progress, drift_report
        ) VALUES (
            :id, :user_id, :connector_type, CAST(:connector_ref AS jsonb), :sync_mode, :scope,
            CAST(:options AS jsonb), 'queued', CAST(:progress AS jsonb), CAST(:drift_report AS jsonb)
        )
        """,
        {
            "id": job_id,
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "sync_mode": sync_mode,
            "scope": scope,
            "options": _json_dumps(options or {}),
            "progress": _json_dumps({"percent": 0, "message": "Queued"}),
            "drift_report": _json_dumps({}),
        },
    )
    return job_id


async def get_job(job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_metadata_jobs
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": job_id, "user_id": user_id},
    )
    return dict(row) if row else None


async def get_active_job(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_metadata_jobs
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_ref @> CAST(:connector_ref AS jsonb)
          AND status IN ('queued', 'running')
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
        },
    )
    return dict(row) if row else None


async def update_job(job_id: str, user_id: str, **fields: Any) -> None:
    sets = ["updated_at = NOW()"]
    values: Dict[str, Any] = {"id": job_id, "user_id": user_id}
    json_cols = {"progress", "drift_report", "connector_ref", "options"}
    for key, val in fields.items():
        if key in json_cols:
            sets.append(f"{key} = CAST(:{key} AS jsonb)")
            values[key] = _json_dumps(val)
        else:
            sets.append(f"{key} = :{key}")
            values[key] = val
    await database.execute(
        f"""
        UPDATE connector_metadata_jobs SET {', '.join(sets)}
        WHERE id = :id AND user_id = :user_id
        """,
        values,
    )


async def save_graph(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    job_id: Optional[str],
    graph_scope: str,
    graph_data: Dict[str, Any],
) -> str:
    graph_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO connector_schema_graphs (
            id, user_id, connector_type, connector_ref, job_id, graph_scope, graph_data
        ) VALUES (
            :id, :user_id, :connector_type, CAST(:connector_ref AS jsonb),
            :job_id, :graph_scope, CAST(:graph_data AS jsonb)
        )
        """,
        {
            "id": graph_id,
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "job_id": job_id,
            "graph_scope": graph_scope,
            "graph_data": _json_dumps(graph_data),
        },
    )
    return graph_id


async def get_graph(graph_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_schema_graphs
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": graph_id, "user_id": user_id},
    )
    return dict(row) if row else None


async def get_latest_graph(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_schema_graphs
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_ref @> CAST(:connector_ref AS jsonb)
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
        },
    )
    return dict(row) if row else None


async def get_schedule(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_schema_schedules
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_ref @> CAST(:connector_ref AS jsonb)
        LIMIT 1
        """,
        {
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
        },
    )
    return dict(row) if row else None


async def upsert_schedule(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    **fields: Any,
) -> str:
    existing = await get_schedule(user_id, connector_type, connector_ref)
    if existing:
        schedule_id = str(existing["id"])
        sets = ["updated_at = NOW()"]
        values: Dict[str, Any] = {
            "id": schedule_id,
            "user_id": user_id,
        }
        json_cols = {"connector_ref", "schedule_config"}
        for key, val in fields.items():
            if key in json_cols:
                sets.append(f"{key} = CAST(:{key} AS jsonb)")
                values[key] = _json_dumps(val)
            else:
                sets.append(f"{key} = :{key}")
                values[key] = val
        await database.execute(
            f"""
            UPDATE connector_schema_schedules SET {', '.join(sets)}
            WHERE id = :id AND user_id = :user_id
            """,
            values,
        )
        return schedule_id

    schedule_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO connector_schema_schedules (
            id, user_id, connector_type, connector_ref,
            schedule_enabled, schedule_frequency, schedule_config, schedule_timezone,
            sync_mode, graph_scope, next_run_at
        ) VALUES (
            :id, :user_id, :connector_type, CAST(:connector_ref AS jsonb),
            :schedule_enabled, :schedule_frequency, CAST(:schedule_config AS jsonb),
            :schedule_timezone, :sync_mode, :graph_scope, :next_run_at
        )
        """,
        {
            "id": schedule_id,
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "schedule_enabled": fields.get("schedule_enabled", False),
            "schedule_frequency": fields.get("schedule_frequency"),
            "schedule_config": _json_dumps(fields.get("schedule_config") or {}),
            "schedule_timezone": fields.get("schedule_timezone"),
            "sync_mode": fields.get("sync_mode", "schema_and_categorize"),
            "graph_scope": fields.get("graph_scope", "neighborhood"),
            "next_run_at": fields.get("next_run_at"),
        },
    )
    return schedule_id


async def list_due_schedules(limit: int = 50) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT * FROM connector_schema_schedules
        WHERE schedule_enabled = TRUE
          AND next_run_at IS NOT NULL
          AND next_run_at <= NOW()
        ORDER BY next_run_at ASC
        LIMIT :limit
        """,
        {"limit": limit},
    )
    return [dict(r) for r in rows or []]
