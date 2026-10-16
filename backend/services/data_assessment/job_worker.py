"""Background worker for connector data assessment jobs."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from services.data_assessment.adapters.registry import get_adapter
from services.data_assessment.adapters.salesforce_adapter import RateLimitPause
from services.data_assessment.assessment_planner import plan_entity_order
from services.data_assessment.progress import (
    PHASE_ATTACHING_GRAPH,
    PHASE_FINALIZING,
    PHASE_LISTING,
    PHASE_METADATA_GAPS,
    PHASE_PROFILING,
    PHASE_SAVING,
    PHASE_SYNTHESIZING,
    build_progress_payload,
)
from services.data_assessment.progress_heartbeat import assessment_progress_heartbeat
from services.data_assessment.report_synthesizer import synthesize_report_sections
from services.data_assessment.scoring import rollup_connector_scores
from services.data_assessment import store
from utils.job_stores import assessment_jobs
from utils.migration_cancel import MigrationCancelledError

logger = logging.getLogger(__name__)

# Keep references so tasks are not GC'd mid-flight; one active worker per job_id.
_assessment_tasks: Dict[str, asyncio.Task] = {}
# Job statuses that should not be overwritten when the worker exits normally.
_STABLE_JOB_STATUSES = frozenset({"completed", "failed", "cancelled", "paused"})


async def _publish_assessment_progress(
    job_id: str,
    user_id: str,
    progress: Dict[str, Any],
) -> None:
    await store.update_job(job_id, user_id, progress=progress)
    await assessment_jobs.update(
        job_id,
        {
            "message": progress.get("message") or progress.get("sub_phase") or "",
            "progress": progress.get("percent", 0),
        },
    )


def _parse_json_field(val: Any) -> Any:
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


def _spawn_assessment_task(job_id: str, user_id: str) -> None:
    existing = _assessment_tasks.get(job_id)
    if existing is not None and not existing.done():
        logger.debug("Assessment worker already active for job %s", job_id)
        return
    task = asyncio.create_task(
        _run_assessment_job_guarded(job_id, user_id),
        name=f"assessment_job:{job_id}",
    )
    _assessment_tasks[job_id] = task

    def _on_done(t: asyncio.Task) -> None:
        _assessment_tasks.pop(job_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "Assessment task %s finished with unhandled error: %s",
                job_id,
                exc,
                exc_info=exc,
            )

    task.add_done_callback(_on_done)


async def _run_assessment_job_guarded(job_id: str, user_id: str) -> None:
    try:
        await run_assessment_job(job_id, user_id)
    except asyncio.CancelledError:
        logger.warning("Assessment job %s cancelled (worker shutdown)", job_id)
        await _mark_job_interrupted(job_id, user_id, "cancelled", "Assessment interrupted")
        raise
    except Exception:
        # run_assessment_job already logs and marks failed
        raise
    finally:
        job = await store.get_job(job_id, user_id)
        if job and (job.get("status") or "") not in _STABLE_JOB_STATUSES:
            await _mark_job_interrupted(
                job_id,
                user_id,
                "failed",
                "Assessment worker stopped unexpectedly",
            )


async def _mark_job_interrupted(
    job_id: str,
    user_id: str,
    status: str,
    message: str,
) -> None:
    try:
        await store.update_job(
            job_id,
            user_id,
            status=status,
            error_summary=message,
        )
        await assessment_jobs.update(job_id, {"status": status, "error": message})
    except Exception:
        logger.exception("Failed to mark assessment job %s as %s", job_id, status)


async def start_assessment_job(job_id: str, user_id: str) -> None:
    await assessment_jobs.set(
        job_id,
        {
            "job_id": job_id,
            "user_id": user_id,
            "status": "queued",
            "progress": 0,
        },
    )
    _spawn_assessment_task(job_id, user_id)


async def run_assessment_job(job_id: str, user_id: str) -> None:
    job = await store.get_job(job_id, user_id)
    if not job:
        return

    connector_type = job["connector_type"]
    connector_ref = _parse_json_field(job["connector_ref"]) or {}
    scope = job["scope"]
    entity_list = _parse_json_field(job.get("entity_list"))
    options = _parse_json_field(job.get("options")) or {}
    checkpoint = _parse_json_field(job.get("checkpoint")) or {"next_index": 0, "partial_report_id": None}

    if job["status"] == "paused":
        return

    await store.update_job(job_id, user_id, status="running")
    await assessment_jobs.update(job_id, {"status": "running", "cancelled": False})

    adapter = get_adapter(connector_type, user_id)

    async def cancel_check() -> bool:
        redis_j = await assessment_jobs.get(job_id, {})
        if redis_j.get("cancelled"):
            return True
        j = await store.get_job(job_id, user_id)
        return j is not None and j.get("status") == "cancelled"

    async def paused_check() -> bool:
        redis_j = await assessment_jobs.get(job_id, {})
        if redis_j.get("status") == "paused":
            return True
        j = await store.get_job(job_id, user_id)
        return j is not None and j.get("status") == "paused"

    try:
        await _publish_assessment_progress(
            job_id,
            user_id,
            build_progress_payload(sub_phase=PHASE_LISTING),
        )
        entities = await adapter.list_assessable_entities(
            scope, connector_ref, entity_list=entity_list
        )
        entities = plan_entity_order(entities, connector_type)
        total = len(entities)
        next_index = int(checkpoint.get("next_index") or 0)
        partial_report_id = checkpoint.get("partial_report_id")

        entity_dicts: List[Dict[str, Any]] = []
        if partial_report_id:
            rep = await store.get_report(partial_report_id, user_id)
            if rep:
                entity_dicts = _parse_json_field(rep.get("entities")) or []

        await _publish_assessment_progress(
            job_id,
            user_id,
            build_progress_payload(
                sub_phase=PHASE_PROFILING,
                completed_entities=next_index,
                total_entities=total,
            ),
        )

        for idx in range(next_index, total):
            if await cancel_check():
                await _finalize(job_id, user_id, connector_type, connector_ref, entity_dicts, True, "cancelled")
                return
            if await paused_check():
                await store.update_job(
                    job_id,
                    user_id,
                    checkpoint={"next_index": idx, "partial_report_id": partial_report_id},
                )
                return

            ent = entities[idx]

            async def _publish(prog: Dict[str, Any]) -> None:
                await _publish_assessment_progress(job_id, user_id, prog)

            def _profiling_payload(*, heartbeat: bool = False) -> Dict[str, Any]:
                return build_progress_payload(
                    sub_phase=PHASE_PROFILING,
                    completed_entities=idx,
                    total_entities=total,
                    current_entity=ent.label,
                    in_entity=True,
                    heartbeat=heartbeat,
                )

            await _publish(_profiling_payload())

            try:
                async with assessment_progress_heartbeat(
                    _publish,
                    lambda: _profiling_payload(heartbeat=True),
                ):
                    profile = await adapter.profile_entity(
                        ent, connector_ref, options, cancel_check
                    )
            except RateLimitPause as rlp:
                until = datetime.now(timezone.utc) + timedelta(minutes=15)
                await store.update_job(
                    job_id,
                    user_id,
                    status="paused",
                    rate_limit_until=until,
                    error_summary=str(rlp),
                    checkpoint={"next_index": idx, "partial_report_id": partial_report_id},
                )
                await assessment_jobs.update(job_id, {"status": "paused"})
                return
            except (asyncio.CancelledError, MigrationCancelledError):
                await _finalize(job_id, user_id, connector_type, connector_ref, entity_dicts, True, "cancelled")
                return

            entity_dicts.append(profile.to_dict())
            await _publish_assessment_progress(
                job_id,
                user_id,
                build_progress_payload(
                    sub_phase=PHASE_SAVING,
                    completed_entities=idx,
                    total_entities=total,
                    current_entity=ent.label,
                ),
            )
            scores = rollup_connector_scores(entity_dicts)
            if not partial_report_id:
                partial_report_id = await store.create_partial_report(
                    job_id=job_id,
                    user_id=user_id,
                    connector_type=connector_type,
                    connector_ref=connector_ref,
                    entities=entity_dicts,
                    scores=scores,
                    is_partial=True,
                )
            else:
                await store.finalize_report(
                    partial_report_id,
                    user_id,
                    scores=scores,
                    sections={},
                    entities=entity_dicts,
                    is_partial=True,
                )
            await store.update_job(
                job_id,
                user_id,
                checkpoint={"next_index": idx + 1, "partial_report_id": partial_report_id},
            )

        await _publish_assessment_progress(
            job_id,
            user_id,
            build_progress_payload(
                sub_phase=PHASE_METADATA_GAPS,
                completed_entities=total,
                total_entities=total,
            ),
        )
        gaps = await adapter.metadata_gaps(
            connector_ref, entities, entity_profiles=entity_dicts
        )
        gap_dicts = [g.to_dict() for g in gaps]
        scores = rollup_connector_scores(entity_dicts, gap_findings=gap_dicts)
        await _publish_assessment_progress(
            job_id,
            user_id,
            build_progress_payload(
                sub_phase=PHASE_SYNTHESIZING,
                completed_entities=total,
                total_entities=total,
            ),
        )

        async def _publish_syn(prog: Dict[str, Any]) -> None:
            await _publish_assessment_progress(job_id, user_id, prog)

        async with assessment_progress_heartbeat(
            _publish_syn,
            lambda: build_progress_payload(
                sub_phase=PHASE_SYNTHESIZING,
                completed_entities=total,
                total_entities=total,
                heartbeat=True,
            ),
        ):
            sections = await synthesize_report_sections(
                connector_type=connector_type,
                scores=scores,
                entity_profiles=entity_dicts,
                gap_findings=gap_dicts,
            )
        try:
            from services.connector_metadata.job_worker import attach_graph_to_assessment_report

            await _publish_assessment_progress(
                job_id,
                user_id,
                build_progress_payload(
                    sub_phase=PHASE_ATTACHING_GRAPH,
                    completed_entities=total,
                    total_entities=total,
                ),
            )
            assessed_ids = [e.entity_id for e in entities]
            sections = await attach_graph_to_assessment_report(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                report_sections=sections,
                entity_ids=assessed_ids,
                graph_scope="neighborhood",
            )
        except Exception as graph_exc:
            logger.warning("Assessment schema graph skipped: %s", graph_exc)
        await _publish_assessment_progress(
            job_id,
            user_id,
            build_progress_payload(
                sub_phase=PHASE_FINALIZING,
                completed_entities=total,
                total_entities=total,
            ),
        )
        is_partial = False
        if partial_report_id:
            await store.finalize_report(
                partial_report_id,
                user_id,
                scores=scores,
                sections=sections,
                entities=entity_dicts,
                is_partial=is_partial,
            )
            report_id = partial_report_id
        else:
            report_id = await store.create_partial_report(
                job_id=job_id,
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                entities=entity_dicts,
                scores=scores,
                is_partial=False,
            )
            await store.finalize_report(
                report_id,
                user_id,
                scores=scores,
                sections=sections,
                entities=entity_dicts,
                is_partial=False,
            )

        await store.update_job(
            job_id,
            user_id,
            status="completed",
            report_id=report_id,
            progress={
                "sub_phase": PHASE_FINALIZING,
                "total_entities": total,
                "completed_entities": total,
                "current_entity": None,
                "percent": 100,
                "message": "Connector assessment finished",
            },
            checkpoint={"next_index": total, "partial_report_id": report_id},
        )
        await assessment_jobs.update(
            job_id,
            {"status": "completed", "report_id": report_id, "progress": 100},
        )
    except Exception as exc:
        logger.exception("Assessment job %s failed", job_id)
        await store.update_job(
            job_id,
            user_id,
            status="failed",
            error_summary=str(exc),
        )
        await assessment_jobs.update(job_id, {"status": "failed", "error": str(exc)})


async def _finalize(
    job_id: str,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entity_dicts: List[Dict[str, Any]],
    is_partial: bool,
    status: str,
) -> None:
    scores = rollup_connector_scores(entity_dicts)
    sections = await synthesize_report_sections(
        connector_type=connector_type,
        scores=scores,
        entity_profiles=entity_dicts,
        gap_findings=[],
    )
    report_id = await store.create_partial_report(
        job_id=job_id,
        user_id=user_id,
        connector_type=connector_type,
        connector_ref=connector_ref,
        entities=entity_dicts,
        scores=scores,
        is_partial=is_partial,
    )
    await store.finalize_report(
        report_id,
        user_id,
        scores=scores,
        sections=sections,
        entities=entity_dicts,
        is_partial=is_partial,
    )
    await store.update_job(job_id, user_id, status=status, report_id=report_id)
    await assessment_jobs.update(job_id, {"status": status, "report_id": report_id})


async def resume_assessment_job(job_id: str, user_id: str) -> None:
    job = await store.get_job(job_id, user_id)
    if not job or job["status"] not in ("paused", "failed", "running", "queued"):
        return
    await store.update_job(job_id, user_id, status="running", rate_limit_until=None)
    await assessment_jobs.update(job_id, {"status": "running", "cancelled": False})
    _spawn_assessment_task(job_id, user_id)


async def recover_orphaned_assessment_jobs() -> None:
    """Re-queue workers for jobs left running after a deploy or crash."""
    rows = await store.list_jobs_by_status(("queued", "running"))
    for row in rows:
        job_id = str(row["id"])
        user_id = str(row["user_id"])
        logger.info("Recovering orphaned assessment job %s", job_id)
        _spawn_assessment_task(job_id, user_id)


async def cancel_assessment_background_tasks() -> None:
    """Best-effort cancel on app shutdown (mirrors export task cleanup)."""
    tasks = [t for t in _assessment_tasks.values() if not t.done()]
    if not tasks:
        return
    for t in tasks:
        t.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=5.0,
        )
    except asyncio.TimeoutError:
        logger.warning("Timed out waiting for assessment tasks to cancel on shutdown")
