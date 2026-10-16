"""Pause / resume / cancel helpers for enterprise graph runs and child DAA jobs."""
from __future__ import annotations

from typing import Any, Dict, List

from services.data_assessment import store as assessment_store
from services.data_assessment.job_worker import resume_assessment_job
from utils.job_stores import assessment_jobs


async def cancel_member_assessment_jobs(
    member_jobs: List[Dict[str, Any]],
    user_id: str,
) -> None:
    for mj in member_jobs or []:
        job_id = mj.get("assessment_job_id")
        if not job_id:
            continue
        if mj.get("status") not in ("running", "paused", "pending"):
            continue
        await assessment_store.update_job(job_id, user_id, status="cancelled")
        await assessment_jobs.update(job_id, {"cancelled": True, "status": "cancelled"})


async def pause_member_assessment_jobs(
    member_jobs: List[Dict[str, Any]],
    user_id: str,
) -> None:
    for mj in member_jobs or []:
        if mj.get("status") != "running":
            continue
        job_id = mj.get("assessment_job_id")
        if not job_id:
            continue
        await assessment_store.update_job(job_id, user_id, status="paused")
        await assessment_jobs.update(job_id, {"status": "paused"})
        mj["status"] = "paused"


async def resume_member_assessment_jobs(
    member_jobs: List[Dict[str, Any]],
    user_id: str,
) -> None:
    for mj in member_jobs or []:
        job_id = mj.get("assessment_job_id")
        if not job_id:
            continue
        job = await assessment_store.get_job(job_id, user_id)
        if not job or job.get("status") not in ("paused", "failed"):
            continue
        await resume_assessment_job(job_id, user_id)
        if mj.get("status") == "paused":
            mj["status"] = "running"
