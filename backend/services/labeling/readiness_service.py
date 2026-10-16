"""Unified connector readiness: metadata sync → entity labeling → profile rollup → agent ready."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from db.connection import database
from services.labeling.connector_profile_service import get_connector_display_name
from services.labeling.connector_ref import (
    connector_instance_id as resolve_connector_instance_id,
    connector_ref_from_instance_id,
    normalize_connector_ref,
)
from services.labeling.connector_setup import get_connector_setup_status
from services.labeling.store import LabelingStore

logger = logging.getLogger(__name__)

PHASE_METADATA = "metadata_sync"
PHASE_LABELING = "entity_labeling"
PHASE_PROFILE = "profile_rollup"
PHASE_READY = "ready"


async def _metadata_sync_in_progress(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    connector_ref: Dict[str, Any],
    profile: Optional[Dict[str, Any]],
) -> bool:
    """True while schema/metadata sync is actively running for this connector."""
    if profile and profile.get("metadata_sync_started_at"):
        return True

    try:
        from services.connector_metadata import store as metadata_store

        active_meta_job = await metadata_store.get_active_job(
            str(user_id),
            connector_type,
            connector_ref,
        )
        if active_meta_job:
            return True
    except Exception as exc:
        logger.debug("metadata job lookup skipped: %s", exc)

    if connector_type == "sql":
        connector_id = connector_ref.get("connector_id") or connector_instance_id
        row = await database.fetch_one(
            """
            SELECT analysis_completed FROM sql_connectors
            WHERE user_id = :user_id AND id::text = :connector_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "connector_id": str(connector_id)},
        )
        if row is not None and not dict(row).get("analysis_completed"):
            return True

    return False


def _parse_progress_blob(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def _resolve_metadata_sync_progress(
    *,
    sync_in_progress: bool,
    profile: Optional[Dict[str, Any]],
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Live object/table counts or percent from profile or active metadata job."""
    if not sync_in_progress:
        return None

    profile_progress = _parse_progress_blob((profile or {}).get("metadata_sync_progress"))
    if profile_progress.get("total") or profile_progress.get("percent") is not None:
        return {
            "processed": int(profile_progress.get("processed") or 0),
            "total": int(profile_progress.get("total") or 0),
            "percent": profile_progress.get("percent"),
            "message": profile_progress.get("message"),
        }

    try:
        from services.connector_metadata import store as metadata_store

        active_meta_job = await metadata_store.get_active_job(
            str(user_id),
            connector_type,
            connector_ref,
        )
        if active_meta_job:
            job_progress = _parse_progress_blob(active_meta_job.get("progress"))
            if job_progress.get("total") or job_progress.get("percent") is not None:
                return {
                    "processed": int(job_progress.get("processed") or 0),
                    "total": int(job_progress.get("total") or 0),
                    "percent": job_progress.get("percent"),
                    "message": job_progress.get("message"),
                }
    except Exception as exc:
        logger.debug("metadata job progress lookup skipped: %s", exc)

    return None


async def on_metadata_sync_started(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> None:
    """Call when schema fetch / DB analysis / metadata sync job begins."""
    ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=resolve_connector_instance_id(connector_type, ref),
    )
    await LabelingStore.mark_metadata_sync_started(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
        display_name=display_name,
    )


async def report_metadata_sync_progress(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
    processed: Optional[int] = None,
    total: Optional[int] = None,
    percent: Optional[int] = None,
    message: Optional[str] = None,
) -> None:
    """Update live metadata sync counts/percent on the connector profile."""
    ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    inst = resolve_connector_instance_id(connector_type, ref)
    await LabelingStore.update_metadata_sync_progress(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        processed=processed,
        total=total,
        percent=percent,
        message=message,
    )


async def _infer_metadata_complete(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> bool:
    if connector_type == "salesforce":
        org_id = connector_ref.get("org_id")
        if not org_id:
            return False
        row = await database.fetch_one(
            """
            SELECT COUNT(*)::int AS c FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
              AND COALESCE(is_deleted, FALSE) = FALSE
            """,
            {"user_id": str(user_id), "org_id": str(org_id)},
        )
        return bool(row and int(dict(row).get("c") or 0) > 0)
    if connector_type == "sql":
        connector_id = connector_ref.get("connector_id")
        if not connector_id:
            return False
        row = await database.fetch_one(
            """
            SELECT analysis_completed FROM sql_connectors
            WHERE user_id = :user_id AND id = :connector_id
            LIMIT 1
            """,
            {"user_id": str(user_id), "connector_id": connector_id},
        )
        return bool(row and dict(row).get("analysis_completed"))
    if connector_type == "google_sheets":
        connection_id = connector_ref.get("connection_id")
        if not connection_id:
            return False
        row = await database.fetch_one(
            """
            SELECT COUNT(*)::int AS c FROM gsheets_extracted_schemas
            WHERE user_id = :user_id AND google_sheets_connection_id = :connection_id
            """,
            {"user_id": str(user_id), "connection_id": connection_id},
        )
        return bool(row and int(dict(row).get("c") or 0) > 0)
    return False


async def get_readiness_state(
    *,
    user_id: str,
    connector_type: str,
    connector_instance_id: str,
    connector_ref: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ref = connector_ref
    if ref is None:
        ref = await normalize_connector_ref(
            user_id,
            connector_type,
            connector_ref_from_instance_id(connector_type, connector_instance_id),
        )
    inst = resolve_connector_instance_id(connector_type, ref)

    profile = await LabelingStore.get_business_profile(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )

    sync_in_progress = await _metadata_sync_in_progress(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        connector_ref=ref,
        profile=profile,
    )

    metadata_done = not sync_in_progress
    if metadata_done:
        metadata_done = bool(profile and profile.get("metadata_sync_completed_at"))
        if not metadata_done:
            metadata_done = await _infer_metadata_complete(
                user_id=str(user_id),
                connector_type=connector_type,
                connector_ref=ref,
            )

    stats = await LabelingStore.get_entity_labeling_stats(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    active_entity_job = await LabelingStore.get_active_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
        job_type="entity_batch",
    )
    active_rollup_job = await LabelingStore.get_active_job(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
        job_type="connector_rollup",
    )

    profile_status = (profile or {}).get("profile_status") or "pending"
    entities_in_flight = (stats.get("pending") or 0) + (stats.get("processing") or 0)

    if not metadata_done:
        phase = PHASE_METADATA
        status = "running"
    elif active_entity_job or entities_in_flight > 0:
        phase = PHASE_LABELING
        status = "running"
    elif active_rollup_job or profile_status not in ("ready",):
        phase = PHASE_PROFILE
        status = "running"
    else:
        phase = PHASE_READY
        status = "completed"

    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )

    is_ready = phase == PHASE_READY
    setup = await get_connector_setup_status(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    metadata_sync_progress = await _resolve_metadata_sync_progress(
        sync_in_progress=sync_in_progress,
        profile=profile,
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
    )
    return {
        "connector_type": connector_type,
        "connector_instance_id": inst,
        "display_name": display_name,
        "phase": phase,
        "status": status,
        "is_ready": is_ready,
        "sync_complete": is_ready,
        "entity_labeling_stats": stats,
        "metadata_sync_progress": metadata_sync_progress,
        "profile_status": profile_status,
        "metadata_sync_complete": metadata_done,
        "metadata_sync_in_progress": sync_in_progress,
        "agent_ready_notified": bool(profile and profile.get("agent_ready_notified_at")),
        **setup,
    }


async def on_metadata_sync_complete(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> None:
    ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    display_name = await get_connector_display_name(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=resolve_connector_instance_id(connector_type, ref),
    )
    await LabelingStore.mark_metadata_sync_complete(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_ref=ref,
        display_name=display_name,
    )


async def check_and_notify_agent_ready(
    *,
    user_id: str,
    connector_type: str,
    connector_ref: Dict[str, Any],
) -> Dict[str, Any]:
    ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
    inst = resolve_connector_instance_id(connector_type, ref)
    state = await get_readiness_state(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
        connector_ref=ref,
    )

    if state["phase"] != PHASE_READY:
        await LabelingStore.update_readiness_phase(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=inst,
            phase=state["phase"],
        )
        return state

    profile = await LabelingStore.get_business_profile(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    if profile and profile.get("agent_ready_notified_at"):
        return state

    user_row = await database.fetch_one(
        "SELECT email FROM users WHERE id = :user_id AND is_deleted = FALSE",
        {"user_id": str(user_id)},
    )
    email = dict(user_row).get("email") if user_row else None
    if not email:
        logger.warning("Agent ready email skipped — no email for user %s", user_id)
        return state

    try:
        from utils.email_utils import send_connector_agent_ready_email

        await send_connector_agent_ready_email(
            to_email=str(email),
            connector_name=state["display_name"],
            connector_type=connector_type,
            needs_connector_setup=bool(state.get("needs_connector_setup")),
            setup_connections_path=state.get("setup_connections_path"),
        )
    except Exception as exc:
        logger.warning("Agent ready email failed for %s/%s: %s", connector_type, inst, exc)
        return state

    await LabelingStore.mark_agent_ready_notified(
        user_id=str(user_id),
        connector_type=connector_type,
        connector_instance_id=inst,
    )
    state["agent_ready_notified"] = True
    return state
