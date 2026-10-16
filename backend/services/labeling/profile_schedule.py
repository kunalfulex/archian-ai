"""Schedule connector profile rollup jobs (avoids circular import with job_worker)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from services.labeling.store import LabelingStore

logger = logging.getLogger(__name__)


async def schedule_connector_profile_rollup(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    force: bool = False,
) -> Optional[str]:
    from services.labeling.connector_profile_service import ConnectorProfileService
    from services.labeling.connector_ref import connector_instance_id
    from services.labeling.job_worker import start_labeling_job

    inst = connector_instance_id(connector_type, connector_ref)
    if not force:
        service = ConnectorProfileService()
        if not await service.should_rollup(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=inst,
        ):
            return None

    active = await LabelingStore.get_active_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        job_type="connector_rollup",
    )
    if active:
        return str(active["id"])

    job_id = await LabelingStore.create_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        entity_keys=[],
        job_type="connector_rollup",
    )
    start_labeling_job(job_id, str(user_id))
    return job_id


def schedule_connector_profile_rollup_fire_and_forget(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    force: bool = False,
) -> None:
    async def _run() -> None:
        try:
            await schedule_connector_profile_rollup(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                force=force,
            )
        except Exception as exc:
            logger.warning("schedule_connector_profile_rollup failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass
