"""Debounced scheduling for label refinement jobs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from services.labeling.config import LabelingConfig
from services.labeling.connector_ref import connector_instance_id, normalize_connector_ref
from services.labeling.job_worker import start_labeling_job
from services.labeling.store import LabelingStore

logger = logging.getLogger(__name__)

_debounce_tasks: Dict[str, asyncio.Task] = {}


async def schedule_label_refinement(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[str]:
    connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    active = await LabelingStore.get_active_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        job_type="label_refinement",
    )
    if active:
        return str(active["id"])

    pending = await LabelingStore.list_pending_evidence(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=connector_instance_id(connector_type, connector_ref),
        limit=1,
    )
    if not pending:
        return None

    job_id = await LabelingStore.create_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        entity_keys=[],
        job_type="label_refinement",
    )
    start_labeling_job(job_id, str(user_id))
    return job_id


def schedule_label_refinement_debounced(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> None:
    inst = connector_instance_id(connector_type, connector_ref)
    key = f"{user_id}:{connector_type}:{inst}"

    async def _run() -> None:
        try:
            await asyncio.sleep(LabelingConfig.refinement_debounce_sec())
            await schedule_label_refinement(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
            )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("schedule_label_refinement debounced failed: %s", exc)
        finally:
            _debounce_tasks.pop(key, None)

    try:
        loop = asyncio.get_running_loop()
        existing = _debounce_tasks.get(key)
        if existing and not existing.done():
            existing.cancel()
        _debounce_tasks[key] = loop.create_task(_run())
    except RuntimeError:
        pass
