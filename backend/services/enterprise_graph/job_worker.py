"""Background worker for enterprise graph assessment runs."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.enterprise_graph import store
from services.enterprise_graph.config import should_run_lake_phase
from services.enterprise_graph.linking import (
    load_member_results_from_assessments,
    run_graph_linking_and_canonical,
    run_lake_proposal_for_graph,
    run_warehouse_proposal_for_graph,
    _lake_proposal_section,
    _warehouse_proposal_section,
)
from services.enterprise_graph.orchestrator import run_member_assessments
from services.enterprise_graph.report_builder import build_enterprise_report_snapshot_async
from services.enterprise_graph.run_progress_updates import (
    graph_rollup_heartbeat,
    publish_graph_run_progress,
)
from services.enterprise_graph.validation import validate_member_list_for_run
from utils.job_stores import enterprise_graph_jobs

logger = logging.getLogger(__name__)

_graph_background_tasks: Dict[str, asyncio.Task] = {}


def _spawn_graph_task(run_id: str, coro) -> None:
    existing = _graph_background_tasks.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(coro, name=f"enterprise_graph_run:{run_id}")
    _graph_background_tasks[run_id] = task

    def _on_done(t: asyncio.Task) -> None:
        _graph_background_tasks.pop(run_id, None)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                "Enterprise graph task %s finished with error: %s",
                run_id,
                exc,
                exc_info=exc,
            )

    task.add_done_callback(_on_done)


async def cancel_graph_background_tasks() -> None:
    tasks = [t for t in _graph_background_tasks.values() if not t.done()]
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
        logger.warning("Timed out waiting for enterprise graph tasks to cancel on shutdown")


async def _safe_update_run(run_id: str, user_id: str, **fields: Any) -> bool:
    """Return False if DB pool is unavailable (shutdown/reload)."""
    from utils.db_shutdown import is_pool_closing_error

    try:
        await store.update_run(run_id, user_id, **fields)
        return True
    except Exception as exc:
        if is_pool_closing_error(exc):
            logger.warning("Skipping graph run DB update during shutdown (run_id=%s)", run_id)
            return False
        raise


async def start_graph_run(run_id: str, user_id: str) -> None:
    await enterprise_graph_jobs.set(
        run_id,
        {"run_id": run_id, "user_id": user_id, "status": "queued", "cancelled": False},
    )
    run = await store.get_run(run_id, user_id)
    run_type = (run or {}).get("run_type") or "assessment"
    if run_type == "remap":
        _spawn_graph_task(run_id, _run_graph_remap_guarded(run_id, user_id))
    elif run_type == "warehouse_redesign":
        _spawn_graph_task(run_id, _run_graph_warehouse_redesign_guarded(run_id, user_id))
    elif run_type == "lake_redesign":
        _spawn_graph_task(run_id, _run_graph_lake_redesign_guarded(run_id, user_id))
    else:
        _spawn_graph_task(run_id, _run_graph_assessment_guarded(run_id, user_id))


async def _run_graph_assessment_guarded(run_id: str, user_id: str) -> None:
    try:
        await run_graph_assessment_job(run_id, user_id)
    except asyncio.CancelledError:
        logger.warning("Enterprise graph assessment %s cancelled (shutdown/reload)", run_id)
        await _safe_update_run(
            run_id,
            user_id,
            status="cancelled",
            error_summary="Interrupted by server restart — please run assessment again",
        )
        raise


async def _finalize_run_with_linking(
    *,
    run_id: str,
    user_id: str,
    graph,
    member_results: List[Dict[str, Any]],
    member_jobs: List[Dict[str, Any]],
    depth: str,
) -> None:
    await publish_graph_run_progress(
        run_id,
        user_id,
        percent=86,
        phase="linking",
        message="Discovering cross-connector entity mappings",
        sub_step="entity_linking",
    )

    async with graph_rollup_heartbeat(
        run_id,
        user_id,
        phase="linking",
        steps=[
            (86, "entity_linking", "Matching entities across connectors"),
            (87, "inventory", "Building unified data inventory"),
            (87, "canonical_build", "Building canonical entity model"),
        ],
    ):
        await publish_graph_run_progress(
            run_id,
            user_id,
            percent=88,
            phase="canonical",
            message="Building canonical model",
            sub_step="canonical_build",
        )
        _, cross_links, canonical_entities, canonical_graph, overlays = await run_graph_linking_and_canonical(
            graph_id=graph.id,
            user_id=user_id,
            members=graph.members,
            member_results=member_results,
            graph_settings=graph.settings or {},
            graph_name=graph.name or "Enterprise graph",
        )

    lake_section = None
    if should_run_lake_phase(graph.settings or {}, graph.members):
        async def _lake_progress(
            percent: int,
            sub_step: str,
            message: str,
            heartbeat: bool = False,
        ) -> None:
            await publish_graph_run_progress(
                run_id,
                user_id,
                percent=percent,
                phase="lake",
                message=message,
                sub_step=sub_step,
                heartbeat=heartbeat,
            )

        lake_proposal = await run_lake_proposal_for_graph(
            graph_id=graph.id,
            user_id=user_id,
            run_id=run_id,
            graph_settings=graph.settings or {},
            members=graph.members,
            member_results=member_results,
            canonical_entities=canonical_entities,
            on_progress=_lake_progress,
        )
        lake_section = _lake_proposal_section(lake_proposal)

    async def _warehouse_progress(
        percent: int,
        sub_step: str,
        message: str,
        heartbeat: bool = False,
    ) -> None:
        await publish_graph_run_progress(
            run_id,
            user_id,
            percent=percent,
            phase="warehouse",
            message=message,
            sub_step=sub_step,
            heartbeat=heartbeat,
        )

    proposal = await run_warehouse_proposal_for_graph(
        graph_id=graph.id,
        user_id=user_id,
        run_id=run_id,
        graph_settings=graph.settings or {},
        members=graph.members,
        canonical_entities=canonical_entities,
        canonical_graph=canonical_graph,
        on_progress=_warehouse_progress,
    )

    async def _report_progress(
        percent: int,
        sub_step: str,
        message: str,
        heartbeat: bool = False,
    ) -> None:
        await publish_graph_run_progress(
            run_id,
            user_id,
            percent=percent,
            phase="report",
            message=message,
            sub_step=sub_step,
            heartbeat=heartbeat,
        )

    snapshot = await build_enterprise_report_snapshot_async(
        graph={"id": graph.id, "name": graph.name},
        member_results=member_results,
        depth=depth,
        cross_links=cross_links,
        mapped_entity_clusters=len(canonical_entities),
        canonical_graph=canonical_graph,
        per_member_overlays=overlays,
        warehouse_proposal_section=_warehouse_proposal_section(proposal),
        lake_proposal_section=lake_section,
        on_progress=_report_progress,
    )

    await store.update_run(
        run_id,
        user_id,
        status="completed",
        report_snapshot=snapshot,
        member_jobs=member_jobs,
        progress={"percent": 100, "phase": "done", "message": "Assessment complete"},
        error_summary=None,
    )
    await store.set_graph_latest_run(graph.id, user_id, run_id)
    await store.clear_assessment_stale(graph.id, user_id)
    await enterprise_graph_jobs.update(run_id, {"status": "completed", "progress": 100})


async def run_graph_assessment_job(run_id: str, user_id: str) -> None:
    run = await store.get_run(run_id, user_id)
    if not run:
        return

    graph = await store.get_graph(run["graph_id"], user_id)
    if not graph:
        await store.update_run(run_id, user_id, status="failed", error_summary="Graph not found")
        return

    members = graph.members
    try:
        validate_member_list_for_run(members)
    except Exception as exc:
        await store.update_run(run_id, user_id, status="failed", error_summary=str(exc))
        return

    await store.update_run(
        run_id,
        user_id,
        status="running",
        progress={"percent": 5, "phase": "assessment", "message": "Starting per-connector assessments"},
    )
    await enterprise_graph_jobs.update(run_id, {"status": "running", "cancelled": False})

    member_jobs: List[Dict[str, Any]] = [
        {
            "member_id": m.id,
            "display_name": m.display_name,
            "status": "pending",
        }
        for m in members
    ]
    await store.update_run(run_id, user_id, member_jobs=member_jobs)

    async def cancel_check() -> bool:
        from utils.db_shutdown import is_db_shutting_down, is_pool_closing_error

        if is_db_shutting_down():
            return True
        try:
            redis_j = await enterprise_graph_jobs.get(run_id, {})
            if redis_j.get("cancelled"):
                return True
            j = await store.get_run(run_id, user_id)
            return j is not None and j.get("status") == "cancelled"
        except Exception as exc:
            if is_pool_closing_error(exc):
                return True
            raise

    async def paused_check() -> bool:
        from utils.db_shutdown import is_db_shutting_down, is_pool_closing_error

        if is_db_shutting_down():
            return True
        try:
            redis_j = await enterprise_graph_jobs.get(run_id, {})
            if redis_j.get("status") == "paused":
                return True
            j = await store.get_run(run_id, user_id)
            return j is not None and j.get("status") == "paused"
        except Exception as exc:
            if is_pool_closing_error(exc):
                return True
            raise

    async def on_started(member_id: str, job_id: str) -> None:
        for mj in member_jobs:
            if mj["member_id"] == member_id:
                mj["status"] = "running"
                mj["assessment_job_id"] = job_id
        await _safe_update_run(
            run_id,
            user_id,
            member_jobs=member_jobs,
            progress={
                "percent": 15,
                "phase": "assessment",
                "message": f"Assessing {next(m.display_name for m in members if m.id == member_id)}",
            },
        )

    async def on_done(member_id: str, report_id: Optional[str], error: Optional[str]) -> None:
        for mj in member_jobs:
            if mj["member_id"] == member_id:
                mj["status"] = "completed" if report_id else "failed"
                mj["assessment_report_id"] = report_id
                if error:
                    mj["error"] = error
        if report_id:
            try:
                await store.update_member_assessment_report(member_id, graph.id, report_id)
            except Exception as exc:
                from utils.db_shutdown import is_pool_closing_error

                if not is_pool_closing_error(exc):
                    raise
        done = sum(1 for mj in member_jobs if mj.get("status") in ("completed", "failed"))
        pct = 15 + int((done / max(len(member_jobs), 1)) * 70)
        await _safe_update_run(
            run_id,
            user_id,
            member_jobs=member_jobs,
            progress={
                "percent": pct,
                "phase": "assessment",
                "message": f"Completed {done}/{len(member_jobs)} connectors",
            },
        )

    try:
        results = await run_member_assessments(
            user_id=user_id,
            members=members,
            depth=run.get("depth") or "partial",
            cancel_check=cancel_check,
            paused_check=paused_check,
            on_member_started=on_started,
            on_member_done=on_done,
        )

        if await cancel_check():
            await _safe_update_run(
                run_id,
                user_id,
                status="cancelled",
                error_summary="Cancelled by user",
            )
            await enterprise_graph_jobs.update(run_id, {"status": "cancelled"})
            return

        await _finalize_run_with_linking(
            run_id=run_id,
            user_id=user_id,
            graph=graph,
            member_results=results,
            member_jobs=member_jobs,
            depth=run.get("depth") or "partial",
        )
    except asyncio.CancelledError:
        await _safe_update_run(
            run_id,
            user_id,
            status="cancelled",
            error_summary="Interrupted by server restart — please run assessment again",
        )
        await enterprise_graph_jobs.update(run_id, {"status": "cancelled"})
        raise
    except Exception as exc:
        from utils.db_shutdown import is_pool_closing_error

        if is_pool_closing_error(exc):
            logger.warning("Enterprise graph run %s stopped (DB pool closed)", run_id)
            return
        logger.exception("Enterprise graph run %s failed", run_id)
        await _safe_update_run(
            run_id,
            user_id,
            status="failed",
            error_summary=str(exc),
            member_jobs=member_jobs,
        )
        await enterprise_graph_jobs.update(run_id, {"status": "failed", "error": str(exc)})


async def _run_graph_remap_guarded(run_id: str, user_id: str) -> None:
    try:
        await run_graph_remap_job(run_id, user_id)
    except asyncio.CancelledError:
        await _safe_update_run(
            run_id,
            user_id,
            status="cancelled",
            error_summary="Interrupted by server restart — please run again",
        )
        raise


async def _run_graph_lake_redesign_guarded(run_id: str, user_id: str) -> None:
    try:
        await run_graph_lake_redesign_job(run_id, user_id)
    except asyncio.CancelledError:
        logger.warning("Enterprise graph lake redesign %s cancelled", run_id)
        await _safe_update_run(
            run_id, user_id, status="failed", error_summary="Lake redesign cancelled"
        )
    except Exception as exc:
        logger.exception("Enterprise graph lake redesign %s failed", run_id)
        await _safe_update_run(run_id, user_id, status="failed", error_summary=str(exc))


async def _run_graph_warehouse_redesign_guarded(run_id: str, user_id: str) -> None:
    try:
        await run_graph_warehouse_redesign_job(run_id, user_id)
    except asyncio.CancelledError:
        await _safe_update_run(
            run_id,
            user_id,
            status="cancelled",
            error_summary="Interrupted by server restart — please run again",
        )
        raise


async def run_graph_remap_job(run_id: str, user_id: str) -> None:
    """Re-run entity linking using latest per-member assessment reports (no new DAA)."""
    run = await store.get_run(run_id, user_id)
    if not run:
        return

    graph = await store.get_graph(run["graph_id"], user_id)
    if not graph:
        await store.update_run(run_id, user_id, status="failed", error_summary="Graph not found")
        return

    members = graph.members
    try:
        validate_member_list_for_run(members)
    except Exception as exc:
        await store.update_run(run_id, user_id, status="failed", error_summary=str(exc))
        return

    await store.update_run(
        run_id,
        user_id,
        status="running",
        progress={"percent": 10, "phase": "linking", "message": "Loading assessment reports"},
    )
    await enterprise_graph_jobs.update(run_id, {"status": "running", "cancelled": False})

    member_jobs: List[Dict[str, Any]] = [
        {
            "member_id": m.id,
            "display_name": m.display_name,
            "status": "skipped",
            "assessment_report_id": m.last_assessment_report_id,
        }
        for m in members
    ]

    try:
        results = await load_member_results_from_assessments(user_id, members)
        missing = [r for r in results if r.get("error")]
        if missing and len(missing) == len(results):
            await store.update_run(
                run_id,
                user_id,
                status="failed",
                error_summary="No assessment reports available. Run a full assessment first.",
                member_jobs=member_jobs,
            )
            await enterprise_graph_jobs.update(run_id, {"status": "failed"})
            return

        await _finalize_run_with_linking(
            run_id=run_id,
            user_id=user_id,
            graph=graph,
            member_results=results,
            member_jobs=member_jobs,
            depth=run.get("depth") or "partial",
        )
    except Exception as exc:
        logger.exception("Enterprise graph remap %s failed", run_id)
        await store.update_run(
            run_id,
            user_id,
            status="failed",
            error_summary=str(exc),
            member_jobs=member_jobs,
        )
        await enterprise_graph_jobs.update(run_id, {"status": "failed", "error": str(exc)})


async def run_graph_lake_redesign_job(run_id: str, user_id: str) -> None:
    """Regenerate lake proposal from latest assessments and canonical model (no new DAA)."""
    run = await store.get_run(run_id, user_id)
    if not run:
        return

    graph = await store.get_graph(run["graph_id"], user_id)
    if not graph:
        await store.update_run(run_id, user_id, status="failed", error_summary="Graph not found")
        return

    if not should_run_lake_phase(graph.settings or {}, graph.members):
        await store.update_run(
            run_id,
            user_id,
            status="failed",
            error_summary="Lake is not enabled or no lake member on this graph.",
        )
        return

    await store.update_run(
        run_id,
        user_id,
        status="running",
        progress={"percent": 20, "phase": "lake", "message": "Regenerating lake proposal"},
    )
    await enterprise_graph_jobs.update(run_id, {"status": "running", "cancelled": False})

    try:
        member_results = await load_member_results_from_assessments(user_id, graph.members)
        canonical_entities = await store.list_canonical_entities(graph.id)
        if not canonical_entities:
            await store.update_run(
                run_id,
                user_id,
                status="failed",
                error_summary="No canonical entities. Run assessment and linking first.",
            )
            await enterprise_graph_jobs.update(run_id, {"status": "failed"})
            return

        proposal = await run_lake_proposal_for_graph(
            graph_id=graph.id,
            user_id=user_id,
            run_id=run_id,
            graph_settings=graph.settings or {},
            members=graph.members,
            member_results=member_results,
            canonical_entities=canonical_entities,
        )

        latest = await store.get_latest_completed_run(graph.id, user_id)
        if latest and latest.get("report_snapshot"):
            snapshot = dict(latest["report_snapshot"])
            sections = dict(snapshot.get("sections") or {})
            sections["lake_proposal"] = _lake_proposal_section(proposal)
            snapshot["sections"] = sections
            await store.update_run(str(latest["id"]), user_id, report_snapshot=snapshot)

        await store.update_run(
            run_id,
            user_id,
            status="completed",
            progress={"percent": 100, "phase": "done", "message": "Lake proposal updated"},
            report_snapshot={"lake_proposal": _lake_proposal_section(proposal)},
        )
        await enterprise_graph_jobs.update(run_id, {"status": "completed", "progress": 100})
    except Exception as exc:
        logger.exception("Lake redesign %s failed", run_id)
        await store.update_run(run_id, user_id, status="failed", error_summary=str(exc))
        await enterprise_graph_jobs.update(run_id, {"status": "failed", "error": str(exc)})


async def run_graph_warehouse_redesign_job(run_id: str, user_id: str) -> None:
    """Regenerate warehouse proposal from latest canonical model (no new DAA)."""
    run = await store.get_run(run_id, user_id)
    if not run:
        return

    graph = await store.get_graph(run["graph_id"], user_id)
    if not graph:
        await store.update_run(run_id, user_id, status="failed", error_summary="Graph not found")
        return

    await store.update_run(
        run_id,
        user_id,
        status="running",
        progress={"percent": 20, "phase": "warehouse", "message": "Regenerating warehouse proposal"},
    )
    await enterprise_graph_jobs.update(run_id, {"status": "running", "cancelled": False})

    try:
        canonical_entities = await store.list_canonical_entities(graph.id)
        if not canonical_entities:
            await store.update_run(
                run_id,
                user_id,
                status="failed",
                error_summary="No canonical entities. Run assessment and linking first.",
            )
            await enterprise_graph_jobs.update(run_id, {"status": "failed"})
            return

        from services.enterprise_graph.inventory import build_inventory_from_member_results
        from services.enterprise_graph.canonical_builder import (
            build_canonical_graph,
            load_member_schema_graphs,
        )

        member_results = await load_member_results_from_assessments(user_id, graph.members)
        inventory = await build_inventory_from_member_results(
            graph.members, member_results, user_id=user_id
        )
        mappings = await store.list_semantic_mappings(graph.id)
        member_graphs = await load_member_schema_graphs(
            user_id=user_id,
            members=graph.members,
            inventory=inventory,
        )
        canonical_graph = build_canonical_graph(
            canonical_entities=canonical_entities,
            mappings=mappings,
            inventory=inventory,
            member_graphs=member_graphs,
        )

        proposal = await run_warehouse_proposal_for_graph(
            graph_id=graph.id,
            user_id=user_id,
            run_id=run_id,
            graph_settings=graph.settings or {},
            members=graph.members,
            canonical_entities=canonical_entities,
            canonical_graph=canonical_graph,
        )

        latest = await store.get_latest_completed_run(graph.id, user_id)
        if latest and latest.get("report_snapshot"):
            snapshot = dict(latest["report_snapshot"])
            sections = dict(snapshot.get("sections") or {})
            sections["warehouse_proposal"] = _warehouse_proposal_section(proposal)
            snapshot["sections"] = sections
            await store.update_run(str(latest["id"]), user_id, report_snapshot=snapshot)

        await store.update_run(
            run_id,
            user_id,
            status="completed",
            progress={"percent": 100, "phase": "done", "message": "Warehouse proposal updated"},
            report_snapshot={"warehouse_proposal": _warehouse_proposal_section(proposal)},
        )
        await enterprise_graph_jobs.update(run_id, {"status": "completed", "progress": 100})
    except Exception as exc:
        logger.exception("Warehouse redesign %s failed", run_id)
        await store.update_run(run_id, user_id, status="failed", error_summary=str(exc))
        await enterprise_graph_jobs.update(run_id, {"status": "failed", "error": str(exc)})
