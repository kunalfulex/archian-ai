"""Merge live per-connector assessment progress into enterprise graph runs."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from services.data_assessment import store as assessment_store
from services.data_assessment.progress import sub_phase_label

# Ordered graph-level phases after per-connector assessments (see job_worker._finalize_run_with_linking).
GRAPH_POST_ASSESSMENT_PHASES = (
    ("linking", "Discovering cross-connector entity mappings"),
    ("canonical", "Building canonical model"),
    ("lake", "Designing lake layout proposal"),
    ("warehouse", "Designing warehouse schema proposal"),
    ("report", "Building unified report"),
    ("done", "Assessment complete"),
)

_SUB_STEP_HINTS = {
    "entity_linking": "Matching entities across connectors",
    "inventory": "Building unified inventory",
    "canonical_build": "Building canonical model",
    "lake_context": "Loading inventory for lake design",
    "lake_bronze": "Designing bronze tables",
    "lake_silver": "Designing silver views",
    "lake_ddl": "Generating lake DDL",
    "lake_save": "Saving lake proposal",
    "warehouse_start": "Starting warehouse design",
    "warehouse_context": "Loading canonical model and mappings",
    "warehouse_ai": "AI warehouse table design in progress",
    "warehouse_rules": "Building warehouse model from rules",
    "warehouse_map": "Mapping sources to warehouse tables",
    "warehouse_ddl": "Generating warehouse DDL",
    "warehouse_save": "Saving warehouse proposal",
    "report_start": "Starting unified report",
    "report_member": "Merging connector assessment into report",
    "report_rollup": "Computing combined readiness scores",
    "report_sections": "Assembling dashboard sections",
    "report_snapshot": "Assembling unified report",
}


def _parse_progress(val: Any) -> Dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _member_progress_fraction(mj: Dict[str, Any]) -> float:
    status = (mj.get("status") or "pending").lower()
    if status in ("completed", "failed"):
        return 1.0
    if status != "running":
        return 0.0
    prog = _parse_progress(mj.get("assessment_progress"))
    pct = prog.get("percent")
    if pct is not None:
        return min(1.0, max(0.0, float(pct) / 100.0))
    total = prog.get("total_entities") or 0
    if total > 0:
        return min(1.0, (prog.get("completed_entities") or 0) / total)
    return 0.05


def _all_members_terminal(member_jobs: List[Dict[str, Any]]) -> bool:
    if not member_jobs:
        return False
    for mj in member_jobs:
        st = (mj.get("status") or "").lower()
        if st not in ("completed", "failed"):
            return False
    return True


def _running_member_message(mj: Dict[str, Any]) -> str:
    name = mj.get("display_name") or "Connector"
    prog = _parse_progress(mj.get("assessment_progress"))
    sub = prog.get("sub_phase")
    child_message = (prog.get("message") or "").strip()
    # Avoid showing job-level "Complete" while the graph member row is still catching up.
    if child_message and child_message.lower() != "complete":
        return f"{name}: {child_message}"
    if child_message and child_message.lower() == "complete":
        return f"{name}: Finishing connector report"

    ent = prog.get("current_entity")
    done = prog.get("completed_entities")
    total = prog.get("total_entities")
    pct = prog.get("percent")

    if sub:
        phase = sub_phase_label(str(sub))
        if ent and str(sub) == "profiling":
            return f"{name}: {phase} {ent}"
        if total and str(sub) in ("profiling", "saving"):
            return f"{name}: {phase} ({done or 0}/{total})"
        if pct is not None:
            return f"{name}: {phase} ({pct}%)"
        return f"{name}: {phase}"

    if ent:
        return f"{name}: {ent}"
    if done is not None and total:
        return f"{name}: {done}/{total} objects"
    return f"{name}: profiling…"


def _display_status(mj: Dict[str, Any]) -> str:
    status = (mj.get("status") or "pending").lower()
    if status == "pending" and not mj.get("assessment_job_id"):
        return "queued"
    return status


async def enrich_run_with_live_progress(
    run: Dict[str, Any],
    user_id: str,
) -> Dict[str, Any]:
    """Attach child DAA progress and recompute assessment-phase percent/message."""
    if not run:
        return run

    status = (run.get("status") or "").lower()
    if status not in ("queued", "running", "paused"):
        return run

    member_jobs: List[Dict[str, Any]] = [
        dict(mj) for mj in (run.get("member_jobs") or [])
    ]
    if not member_jobs:
        return run

    for mj in member_jobs:
        mj["display_status"] = _display_status(mj)
        job_id = mj.get("assessment_job_id")
        if job_id and mj.get("status") == "running":
            child = await assessment_store.get_job(str(job_id), user_id)
            if child:
                prog = _parse_progress(child.get("progress"))
                child_status = (child.get("status") or "").lower()
                mj["assessment_progress"] = prog
                if child_status == "paused":
                    mj["status"] = "paused"
                    mj["display_status"] = "paused"
                elif child_status in ("failed", "cancelled"):
                    mj["status"] = "failed"
                    mj["display_status"] = "failed"
                    mj["error"] = child.get("error_summary") or child_status
                elif child_status == "completed":
                    # Child DAA finished; graph member_jobs may lag until on_done runs.
                    mj["status"] = "completed"
                    mj["display_status"] = "completed"
                    if (prog.get("message") or "").strip().lower() == "complete":
                        mj["assessment_progress"] = {
                            **prog,
                            "message": "Connector assessment finished",
                        }

    progress = dict(_parse_progress(run.get("progress")))
    phase = (progress.get("phase") or "").lower()

    # Post-connector phases: keep persisted percent/message (linking → done).
    if phase and phase not in ("assessment", "queued", "rollup", ""):
        if not progress.get("message"):
            for phase_id, default_msg in GRAPH_POST_ASSESSMENT_PHASES:
                if phase_id == phase:
                    progress["message"] = default_msg
                    break
        sub_step = progress.get("sub_step")
        if sub_step and _SUB_STEP_HINTS.get(str(sub_step)):
            progress["detail"] = _SUB_STEP_HINTS[str(sub_step)]
        elif sub_step:
            progress["detail"] = str(sub_step).replace("_", " ").title()
    elif _all_members_terminal(member_jobs) and status in ("running", "queued"):
        # All connector jobs done; graph worker is in rollup (linking) shortly.
        progress["percent"] = max(progress.get("percent") or 0, 85)
        progress["message"] = (
            progress.get("message")
            or "All connectors finished — starting cross-connector mapping…"
        )
        progress["phase"] = "rollup"
    elif phase == "assessment" or status in ("queued", "running", "paused"):
        fracs = [_member_progress_fraction(mj) for mj in member_jobs]
        pct = 15 + int((sum(fracs) / max(len(member_jobs), 1)) * 70)
        progress["percent"] = min(84, max(progress.get("percent") or 0, pct))

        running_parts = [
            _running_member_message(mj)
            for mj in member_jobs
            if mj.get("status") == "running"
        ]
        queued = [
            mj.get("display_name") or "Connector"
            for mj in member_jobs
            if mj.get("display_status") == "queued"
        ]
        msg_parts: List[str] = []
        if running_parts:
            msg_parts.append("Assessing " + "; ".join(running_parts[:3]))
        if queued:
            msg_parts.append(
                f"Waiting ({len(queued)}): {', '.join(queued)}"
                + " — runs 2 connectors at a time"
            )
        if status == "paused":
            msg_parts = ["Paused"] + msg_parts
        if msg_parts:
            progress["message"] = " · ".join(msg_parts)

    run = dict(run)
    run["member_jobs"] = member_jobs
    run["progress"] = progress
    return run
