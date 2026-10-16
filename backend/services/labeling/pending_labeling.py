"""Queue entity labeling for pending/stale/missing semantics (all connector types)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.labeling.connector_ref import connector_instance_id, normalize_connector_ref
from services.labeling.schedule import schedule_entity_labeling
from services.labeling.stale_detection import collect_entities_needing_labeling, hashes_for_keys

logger = logging.getLogger(__name__)


async def schedule_pending_entity_labeling(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    current_hashes: Dict[str, str],
    drift_keys: Optional[List[str]] = None,
    legacy_keys: Optional[List[str]] = None,
) -> int:
    """
    Queue background labeling for entities needing semantics (drift, legacy gap, or stale/missing).

    Returns the number of entity keys queued (0 if none).
    Label execution still uses connector-specific prompts in EntityLabelingService.
    """
    ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    inst = connector_instance_id(connector_type, ref)
    to_label = await collect_entities_needing_labeling(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        drift_keys=list(drift_keys or []),
        current_hashes=current_hashes,
        legacy_keys=list(legacy_keys or []) if legacy_keys else None,
    )
    if not to_label:
        return 0

    job_id = await schedule_entity_labeling(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
        entity_keys=to_label,
        entity_hashes=hashes_for_keys(to_label, current_hashes),
    )
    if job_id:
        logger.info(
            "Queued labeling for %s/%s: %d entities (job %s)",
            connector_type,
            inst,
            len(to_label),
            job_id,
        )
        return len(to_label)
    return 0


def schedule_pending_entity_labeling_fire_and_forget(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    current_hashes: Dict[str, str],
    drift_keys: Optional[List[str]] = None,
    legacy_keys: Optional[List[str]] = None,
) -> None:
    async def _run() -> None:
        try:
            await schedule_pending_entity_labeling(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                current_hashes=current_hashes,
                drift_keys=drift_keys,
                legacy_keys=legacy_keys,
            )
        except Exception as exc:
            logger.warning(
                "schedule_pending_entity_labeling failed for %s: %s",
                connector_type,
                exc,
            )

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass
