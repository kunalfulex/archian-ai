"""Schedule background entity labeling (non-blocking)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.labeling.connector_ref import normalize_connector_ref
from services.labeling.job_worker import start_labeling_job
from services.labeling.store import LabelingStore

logger = logging.getLogger(__name__)


async def schedule_entity_labeling(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entity_keys: Optional[List[str]] = None,
    entity_hashes: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """
    Queue entity labeling in the background. Returns job_id or None if empty.
    Never blocks the caller beyond a quick DB write.
    """
    connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    keys = [k for k in (entity_keys or []) if k]
    if not keys:
        return None

    hash_map = entity_hashes or {}
    for entity_key in keys:
        try:
            await LabelingStore.upsert_entity_pending(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_ref=connector_ref,
                entity_key=entity_key,
                schema_hash=hash_map.get(entity_key),
            )
        except Exception as exc:
            logger.warning("upsert_entity_pending failed for %s: %s", entity_key, exc)

    active = await LabelingStore.get_active_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
    )
    if active:
        await LabelingStore.merge_entity_keys_into_active_job(
            str(active["id"]),
            str(user_id),
            keys,
        )
        start_labeling_job(str(active["id"]), str(user_id))
        return str(active["id"])

    job_id = await LabelingStore.create_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=connector_ref,
        entity_keys=keys,
    )
    start_labeling_job(job_id, str(user_id))
    return job_id


def schedule_entity_labeling_fire_and_forget(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    entity_keys: Optional[List[str]] = None,
    entity_hashes: Optional[Dict[str, str]] = None,
) -> None:
    """Schedule from sync paths without awaiting (uses create_task)."""

    async def _run() -> None:
        try:
            await schedule_entity_labeling(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                entity_keys=entity_keys,
                entity_hashes=entity_hashes,
            )
        except Exception as exc:
            logger.warning("schedule_entity_labeling failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass


async def schedule_salesforce_connector_labeling(
    *,
    user_id: str,
    connector_id: str,
) -> Optional[str]:
    """Label pending Salesforce objects for a connection (background)."""
    from db.connection import database
    from services.labeling.connector_ref import normalize_connector_ref

    connector_ref = await normalize_connector_ref(
        user_id, "salesforce", {"connector_id": connector_id}
    )
    org_id = connector_ref["org_id"]
    rows = await database.fetch_all(
        """
        SELECT object_api_name FROM salesforce_objects
        WHERE user_id = :user_id AND org_id = :org_id
          AND COALESCE(is_deleted, FALSE) = FALSE
          AND category IS NULL
        """,
        {"user_id": str(user_id), "org_id": org_id},
    )
    keys = [dict(r).get("object_api_name") for r in rows or [] if dict(r).get("object_api_name")]
    if not keys:
        return None
    return await schedule_entity_labeling(
        user_id=str(user_id),
        connector_type="salesforce",
        connector_ref=connector_ref,
        entity_keys=keys,
    )


def schedule_salesforce_connector_labeling_fire_and_forget(
    *,
    user_id: str,
    connector_id: str,
) -> None:
    async def _run() -> None:
        try:
            await schedule_salesforce_connector_labeling(
                user_id=str(user_id),
                connector_id=connector_id,
            )
        except Exception as exc:
            logger.warning("schedule_salesforce_connector_labeling failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass


async def schedule_salesforce_connector_labeling_by_org_id(
    *,
    user_id: str,
    org_id: str,
) -> Optional[str]:
    """Resolve token from org_id and queue labeling (schema sync paths)."""
    from services.connectors.salesforce_ref import resolve_salesforce_token

    token = await resolve_salesforce_token(str(user_id), org_id)
    if not token:
        return None
    return await schedule_salesforce_connector_labeling(
        user_id=str(user_id),
        connector_id=str(token["connector_id"]),
    )


def schedule_salesforce_connector_labeling_by_org_id_fire_and_forget(
    *,
    user_id: str,
    org_id: str,
) -> None:
    async def _run() -> None:
        try:
            await schedule_salesforce_connector_labeling_by_org_id(
                user_id=str(user_id),
                org_id=org_id,
            )
        except Exception as exc:
            logger.warning("schedule_salesforce_connector_labeling_by_org_id failed: %s", exc)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_run())
    except RuntimeError:
        pass
