"""Fan-out per-member data assessments for an enterprise graph run."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.data_assessment import store as assessment_store
from services.data_assessment.job_worker import start_assessment_job
from services.enterprise_graph.config import (
    assessment_job_timeout_seconds,
    assessment_poll_interval_seconds,
    daa_concurrency,
    depth_to_assessment_scope,
)
from services.enterprise_graph.connector_ref import (
    assessment_connector_ref,
    sheet_entity_allowlist_from_ref,
)
from services.enterprise_graph.models import GraphMember

logger = logging.getLogger(__name__)


async def wait_for_assessment_job(
    job_id: str,
    user_id: str,
    *,
    cancel_check,
    paused_check=None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    timeout = timeout_seconds or assessment_job_timeout_seconds()
    poll = assessment_poll_interval_seconds()
    elapsed = 0.0
    while elapsed < timeout:
        if await cancel_check():
            raise asyncio.CancelledError("Enterprise graph run cancelled")
        if paused_check and await paused_check():
            await asyncio.sleep(poll)
            continue
        try:
            job = await assessment_store.get_job(job_id, user_id)
        except Exception as exc:
            from utils.db_shutdown import is_pool_closing_error

            if is_pool_closing_error(exc):
                raise asyncio.CancelledError("Server shutting down") from exc
            raise
        if not job:
            return {"status": "failed", "error_summary": "Assessment job not found"}
        status = job.get("status")
        if status == "completed":
            return job
        if status in ("failed", "cancelled"):
            return job
        if status == "paused":
            await asyncio.sleep(poll)
            continue
        await asyncio.sleep(poll)
        elapsed += poll
    return {"status": "failed", "error_summary": "Assessment timed out"}


async def start_member_assessment(
    user_id: str,
    member: GraphMember,
    scope: str,
) -> str:
    ref = assessment_connector_ref(member.connector_type, member.connector_ref)
    entity_list = None
    if (member.connector_type or "").lower() == "google_sheets":
        entity_list = sheet_entity_allowlist_from_ref(member.connector_ref)
    job_id = await assessment_store.create_job(
        user_id=user_id,
        connector_type=member.connector_type,
        connector_ref=ref,
        scope=scope,
        entity_list=entity_list,
        options={"enterprise_graph_member_id": member.id},
    )
    await start_assessment_job(job_id, user_id)
    return job_id


async def run_member_assessments(
    *,
    user_id: str,
    members: List[GraphMember],
    depth: str,
    cancel_check,
    paused_check=None,
    on_member_started,
    on_member_done,
) -> List[Dict[str, Any]]:
    scope = depth_to_assessment_scope(depth)
    sem = asyncio.Semaphore(daa_concurrency())
    results: List[Dict[str, Any]] = []

    poll = assessment_poll_interval_seconds()

    async def _wait_until_running() -> None:
        while paused_check and await paused_check():
            if await cancel_check():
                raise asyncio.CancelledError("Enterprise graph run cancelled")
            await asyncio.sleep(poll)

    async def _one(member: GraphMember) -> Dict[str, Any]:
        async with sem:
            await _wait_until_running()
            base = {
                "member_id": member.id,
                "display_name": member.display_name,
                "connector_type": member.connector_type,
                "role": member.role,
            }
            try:
                job_id = await start_member_assessment(user_id, member, scope)
                await on_member_started(member.id, job_id)
                job = await wait_for_assessment_job(
                    job_id,
                    user_id,
                    cancel_check=cancel_check,
                    paused_check=paused_check,
                )
                if job.get("status") != "completed":
                    err = job.get("error_summary") or f"Assessment {job.get('status')}"
                    await on_member_done(member.id, None, err)
                    return {**base, "error": err, "assessment_job_id": job_id}

                report_id = job.get("report_id")
                if not report_id:
                    await on_member_done(member.id, None, "No report produced")
                    return {**base, "error": "No report produced", "assessment_job_id": job_id}

                report = await assessment_store.get_report(str(report_id), user_id)
                await on_member_done(member.id, str(report_id), None)
                return {
                    **base,
                    "assessment_report_id": str(report_id),
                    "assessment_job_id": job_id,
                    "report": dict(report) if report else {},
                }
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Member assessment failed for %s", member.id)
                await on_member_done(member.id, None, str(exc))
                return {**base, "error": str(exc)}

    tasks = [asyncio.create_task(_one(m)) for m in members]
    for coro in asyncio.as_completed(tasks):
        results.append(await coro)
    return results
