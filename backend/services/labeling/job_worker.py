"""Background worker for connector entity labeling and profile rollup jobs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.labeling.connector_profile_service import ConnectorProfileService, get_connector_display_name
from services.labeling.entity_labeling_service import EntityLabelingService
from services.labeling.connector_ref import connector_instance_id, normalize_connector_ref
from services.labeling.profile_schedule import schedule_connector_profile_rollup_fire_and_forget
from services.labeling.store import LabelingStore

logger = logging.getLogger(__name__)


async def run_connector_profile_job(job_id: str, user_id: str) -> None:
    job = await LabelingStore.get_job(job_id, user_id)
    if not job:
        return
    if job.get("status") not in ("queued", "running"):
        return

    connector_type = job["connector_type"]
    connector_ref = job.get("connector_ref") or {}
    connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)

    await LabelingStore.update_job(
        job_id,
        user_id,
        status="running",
        progress={"message": "Building connector business profile"},
    )

    try:
        service = ConnectorProfileService()
        result = await service.rollup_connector_profile(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            force=True,
        )
        if result:
            await LabelingStore.update_job(
                job_id,
                user_id,
                status="completed",
                progress={"message": "Connector profile ready", "profile": result},
            )
        else:
            inst = connector_instance_id(connector_type, connector_ref)
            stats = await LabelingStore.get_entity_labeling_stats(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_instance_id=inst,
            )
            display_name = await get_connector_display_name(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_instance_id=inst,
            )
            ready_count = int(stats.get("ready") or 0)
            await LabelingStore.save_ai_profile(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_ref=connector_ref,
                display_name=display_name,
                ai_profile={
                    "synopsis": (
                        f"Archian AI indexed {ready_count} entities for this connection."
                        if ready_count
                        else "Archian AI completed schema indexing for this connection."
                    ),
                    "source": "rollup_skipped",
                },
                profile_status="ready",
            )
            await LabelingStore.update_job(
                job_id,
                user_id,
                status="completed",
                progress={"message": "Profile rollup skipped (insufficient labeled entities)"},
            )
        from services.labeling.readiness_service import check_and_notify_agent_ready

        await check_and_notify_agent_ready(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
        )
    except Exception as exc:
        logger.exception("Connector profile job %s failed", job_id)
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="failed",
            error_summary=str(exc)[:1000],
            progress={"message": str(exc)[:200]},
        )


async def run_label_refinement_job(job_id: str, user_id: str) -> None:
    job = await LabelingStore.get_job(job_id, user_id)
    if not job:
        return
    if job.get("status") not in ("queued", "running"):
        return

    connector_type = job["connector_type"]
    connector_ref = job.get("connector_ref") or {}
    connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)

    await LabelingStore.update_job(
        job_id,
        user_id,
        status="running",
        progress={"message": "Refining labels from execution evidence"},
    )

    try:
        from services.labeling.label_learning_service import LabelLearningService

        result = await LabelLearningService.refine_connector_labels(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
        )
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="completed",
            progress={"message": "Label refinement complete", **result},
        )
    except Exception as exc:
        logger.exception("Label refinement job %s failed", job_id)
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="failed",
            error_summary=str(exc)[:1000],
            progress={"message": str(exc)[:200]},
        )


async def run_labeling_job(job_id: str, user_id: str) -> None:
    job = await LabelingStore.get_job(job_id, user_id)
    if not job:
        return
    if job.get("status") not in ("queued", "running"):
        return

    job_type = job.get("job_type") or "entity_batch"
    if job_type == "connector_rollup":
        await run_connector_profile_job(job_id, user_id)
        return
    if job_type == "label_refinement":
        await run_label_refinement_job(job_id, user_id)
        return

    connector_type = job["connector_type"]
    connector_ref = job.get("connector_ref") or {}
    connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    entity_keys: List[str] = list(job.get("entity_keys") or [])
    if not entity_keys:
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="completed",
            progress={"labeled": 0, "total": 0, "message": "No entities to label"},
        )
        schedule_connector_profile_rollup_fire_and_forget(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            force=True,
        )
        return

    await LabelingStore.update_job(
        job_id,
        user_id,
        status="running",
        progress={"labeled": 0, "total": len(entity_keys), "message": "Labeling entities"},
    )

    service = EntityLabelingService()

    async def on_progress(*, labeled: int, total: int, failed: int, entity_key: str) -> None:
        await LabelingStore.update_job(
            job_id,
            user_id,
            progress={
                "labeled": labeled,
                "total": total,
                "failed": failed,
                "message": f"Labeling {entity_key}",
            },
        )

    try:
        result = await service.label_entities_batch(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_keys=entity_keys,
            on_progress=on_progress,
        )
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="completed",
            progress={
                **result,
                "message": "Labeling complete",
            },
            error_summary=(
                f"{result.get('failed', 0)} entities failed"
                if result.get("failed")
                else None
            ),
        )
        schedule_connector_profile_rollup_fire_and_forget(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            force=True,
        )
    except Exception as exc:
        logger.exception("Labeling job %s failed", job_id)
        await LabelingStore.update_job(
            job_id,
            user_id,
            status="failed",
            error_summary=str(exc)[:1000],
            progress={"message": str(exc)[:200]},
        )


def start_labeling_job(job_id: str, user_id: str) -> None:
    """Fire-and-forget labeling job (non-blocking)."""
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(run_labeling_job(job_id, user_id))
    except RuntimeError:
        pass
