"""Postgres persistence for assessment jobs and reports."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.connection import database


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


async def create_job(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    scope: str,
    entity_list: Optional[List[str]],
    options: Dict[str, Any],
) -> str:
    job_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO connector_assessment_jobs (
            id, user_id, connector_type, connector_ref, scope, entity_list,
            options, status, progress, checkpoint
        ) VALUES (
            :id, :user_id, :connector_type, CAST(:connector_ref AS jsonb), :scope,
            CAST(:entity_list AS jsonb), CAST(:options AS jsonb),
            'queued', CAST(:progress AS jsonb), CAST(:checkpoint AS jsonb)
        )
        """,
        {
            "id": job_id,
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "scope": scope,
            "entity_list": _json_dumps(entity_list) if entity_list is not None else None,
            "options": _json_dumps(options or {}),
            "progress": _json_dumps(
                {"total_entities": 0, "completed_entities": 0, "current_entity": None, "percent": 0}
            ),
            "checkpoint": _json_dumps({"next_index": 0, "partial_report_id": None}),
        },
    )
    return job_id


def _parse_json_field(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


async def get_job(job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_assessment_jobs
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": job_id, "user_id": user_id},
    )
    if not row:
        return None
    job = dict(row)
    for key in ("connector_ref", "entity_list", "options", "progress", "checkpoint"):
        if key in job and job[key] is not None:
            job[key] = _parse_json_field(job[key])
    return job


async def list_jobs_by_status(
    statuses: tuple[str, ...],
) -> List[Dict[str, Any]]:
    if not statuses:
        return []
    rows = await database.fetch_all(
        """
        SELECT id, user_id, status, connector_type, created_at
        FROM connector_assessment_jobs
        WHERE status = ANY(:statuses)
        ORDER BY created_at ASC
        """,
        {"statuses": list(statuses)},
    )
    return [dict(r) for r in rows]


async def get_active_job_for_connector(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Latest in-flight assessment job for this connector (queued, running, or paused)."""
    row = await database.fetch_one(
        """
        SELECT * FROM connector_assessment_jobs
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_ref @> CAST(:connector_ref AS jsonb)
          AND status IN ('queued', 'running', 'paused')
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
    json_cols = {
        "progress",
        "checkpoint",
        "connector_ref",
        "entity_list",
        "options",
    }
    for key, val in fields.items():
        if key in json_cols:
            sets.append(f"{key} = CAST(:{key} AS jsonb)")
            values[key] = _json_dumps(val)
        else:
            sets.append(f"{key} = :{key}")
            values[key] = val
    await database.execute(
        f"""
        UPDATE connector_assessment_jobs SET {', '.join(sets)}
        WHERE id = :id AND user_id = :user_id
        """,
        values,
    )


async def create_partial_report(
    *,
    job_id: str,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entities: List[Dict[str, Any]],
    scores: Dict[str, Any],
    is_partial: bool = True,
) -> str:
    report_id = str(uuid.uuid4())
    await database.execute(
        """
        INSERT INTO connector_assessment_reports (
            id, job_id, user_id, connector_type, connector_ref,
            is_partial, scores, sections, entities
        ) VALUES (
            :id, :job_id, :user_id, :connector_type, CAST(:connector_ref AS jsonb),
            :is_partial, CAST(:scores AS jsonb), CAST(:sections AS jsonb), CAST(:entities AS jsonb)
        )
        """,
        {
            "id": report_id,
            "job_id": job_id,
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "is_partial": is_partial,
            "scores": _json_dumps(scores),
            "sections": _json_dumps({}),
            "entities": _json_dumps(entities),
        },
    )
    return report_id


async def finalize_report(
    report_id: str,
    user_id: str,
    *,
    scores: Dict[str, Any],
    sections: Dict[str, Any],
    entities: List[Dict[str, Any]],
    is_partial: bool,
) -> None:
    await database.execute(
        """
        UPDATE connector_assessment_reports
        SET scores = CAST(:scores AS jsonb),
            sections = CAST(:sections AS jsonb),
            entities = CAST(:entities AS jsonb),
            is_partial = :is_partial
        WHERE id = :id AND user_id = :user_id
        """,
        {
            "id": report_id,
            "user_id": user_id,
            "scores": _json_dumps(scores),
            "sections": _json_dumps(sections),
            "entities": _json_dumps(entities),
            "is_partial": is_partial,
        },
    )


async def get_report(report_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT * FROM connector_assessment_reports
        WHERE id = :id AND user_id = :user_id
        """,
        {"id": report_id, "user_id": user_id},
    )
    return dict(row) if row else None


async def list_reports(
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    limit: int = 20,
) -> List[Dict[str, Any]]:
    rows = await database.fetch_all(
        """
        SELECT id, job_id, connector_type, connector_ref, is_partial, scores,
               sections, created_at
        FROM connector_assessment_reports
        WHERE user_id = :user_id
          AND connector_type = :connector_type
          AND connector_ref @> CAST(:connector_ref AS jsonb)
        ORDER BY created_at DESC
        LIMIT :limit
        """,
        {
            "user_id": user_id,
            "connector_type": connector_type,
            "connector_ref": _json_dumps(connector_ref),
            "limit": limit,
        },
    )
    return [dict(r) for r in rows or []]
