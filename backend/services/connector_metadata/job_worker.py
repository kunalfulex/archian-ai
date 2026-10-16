"""Background worker for connector metadata sync jobs."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from services.connector_metadata.adapters.registry import get_metadata_adapter
from services.connector_metadata.graph_builder import build_schema_graph
from services.connector_metadata import store
from utils.job_stores import metadata_sync_jobs

logger = logging.getLogger(__name__)


def _parse_json(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except json.JSONDecodeError:
            return val
    return val


async def start_metadata_sync_job(job_id: str, user_id: str) -> None:
    await metadata_sync_jobs.set(
        job_id,
        {"job_id": job_id, "user_id": user_id, "status": "queued", "progress": 0},
    )
    asyncio.create_task(run_metadata_sync_job(job_id, user_id))


async def run_metadata_sync_job(job_id: str, user_id: str) -> None:
    job = await store.get_job(job_id, user_id)
    if not job:
        return

    connector_type = job["connector_type"]
    connector_ref = _parse_json(job.get("connector_ref")) or {}
    sync_mode = job.get("sync_mode") or "schema_and_categorize"
    options = _parse_json(job.get("options")) or {}
    build_graph = bool(options.get("build_graph", True))
    graph_scope = options.get("graph_scope") or "neighborhood"
    entity_ids = options.get("entity_ids")

    await store.update_job(job_id, user_id, status="running")
    await metadata_sync_jobs.update(job_id, {"status": "running"})

    try:
        from services.labeling.readiness_service import on_metadata_sync_started

        await on_metadata_sync_started(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
        )
    except Exception as readiness_exc:
        logger.warning("Metadata sync-start readiness hook failed: %s", readiness_exc)

    adapter = get_metadata_adapter(connector_type, user_id)

    async def on_progress(*, percent: int, message: str) -> None:
        progress = {"percent": percent, "message": message}
        await store.update_job(job_id, user_id, progress=progress)
        await metadata_sync_jobs.update(job_id, {"progress": percent, "message": message})
        try:
            from services.labeling.connector_ref import connector_instance_id
            from services.labeling.readiness_service import report_metadata_sync_progress

            inst = connector_instance_id(connector_type, connector_ref)
            await report_metadata_sync_progress(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_ref=connector_ref,
                percent=percent,
                message=message,
            )
        except Exception as progress_exc:
            logger.debug("Metadata sync profile progress update failed: %s", progress_exc)

    try:
        drift = await adapter.sync_schema(
            connector_ref,
            sync_mode=sync_mode,
            on_progress=on_progress,
        )
        drift_dict = drift.to_dict()

        graph_id = None
        if build_graph:
            graph = await build_schema_graph(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                graph_scope=graph_scope,
                entity_ids=entity_ids,
                include_deleted=True,
            )
            graph_id = await store.save_graph(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                job_id=job_id,
                graph_scope=graph_scope,
                graph_data=graph.to_dict(),
            )

        await store.update_job(
            job_id,
            user_id,
            status="completed",
            drift_report=drift_dict,
            result_graph_id=graph_id,
            progress={"percent": 100, "message": "Complete"},
        )
        await metadata_sync_jobs.update(
            job_id,
            {"status": "completed", "progress": 100, "graph_id": graph_id},
        )

        from services.labeling.readiness_service import on_metadata_sync_complete

        try:
            await on_metadata_sync_complete(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_ref=connector_ref,
            )
        except Exception as readiness_exc:
            logger.warning("Metadata readiness hook failed: %s", readiness_exc)

        sched = await store.get_schedule(user_id, connector_type, connector_ref)
        if sched:
            await database_touch_schedule_success(str(sched["id"]), job_id, graph_id)

    except Exception as exc:
        logger.exception("Metadata sync job %s failed", job_id)
        await store.update_job(
            job_id,
            user_id,
            status="failed",
            error_summary=str(exc),
            progress={"percent": 0, "message": str(exc)},
        )
        await metadata_sync_jobs.update(job_id, {"status": "failed", "error": str(exc)})
        try:
            from services.labeling.connector_ref import connector_instance_id
            from services.labeling.store import LabelingStore

            inst = connector_instance_id(connector_type, connector_ref)
            await LabelingStore.clear_metadata_sync_in_progress(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_instance_id=inst,
            )
        except Exception as clear_exc:
            logger.warning("Failed to clear metadata sync-in-progress: %s", clear_exc)


async def database_touch_schedule_success(
    schedule_id: str, job_id: str, graph_id: Optional[str]
) -> None:
    from db.connection import database

    await database.execute(
        """
        UPDATE connector_schema_schedules
        SET last_success_at = NOW(),
            last_error = NULL,
            last_job_id = :job_id,
            active_graph_id = :graph_id,
            updated_at = NOW()
        WHERE id = :id
        """,
        {"id": schedule_id, "job_id": job_id, "graph_id": graph_id},
    )


async def attach_graph_to_assessment_report(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    report_sections: Dict[str, Any],
    entity_ids: Optional[list] = None,
    graph_scope: str = "neighborhood",
) -> Dict[str, Any]:
    """Add schema_graph section for assessment reports."""
    graph = await build_schema_graph(
        user_id=user_id,
        connector_type=connector_type,
        connector_ref=connector_ref,
        graph_scope=graph_scope,
        entity_ids=entity_ids,
        include_deleted=True,
    )
    out = dict(report_sections or {})
    out["schema_graph"] = graph.to_dict()
    return out
