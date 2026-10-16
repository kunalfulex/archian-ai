"""Build execution observations after agent tool steps."""
from __future__ import annotations

from typing import Any, Dict, Optional

from agent.observation import ExecutionObservation, FailureReport, TopError
from agent.recovery_policy import migration_batch_in_progress, observation_status_from_rate
from agent.session import AgentSession
from agent.tools.base import ToolResult


async def build_observation_from_batch(
    *,
    tool_id: str,
    batch_id: str,
    step_id: Optional[str] = None,
    job_id: Optional[str] = None,
    mapping_id: Optional[str] = None,
) -> ExecutionObservation:
    from services.migration_records_service import MigrationRecordsService

    summary = await MigrationRecordsService.summarize_batch_failures(batch_id)
    if not summary:
        return ExecutionObservation(
            step_id=step_id,
            tool_id=tool_id,
            status="submitted",
            batch_id=batch_id,
            job_id=job_id,
            mapping_id=mapping_id,
        )

    batch_status = summary.get("batch_status")
    if summary.get("ready_for_recovery") is False:
        total = int(summary.get("total_records") or 0)
        ok = int(summary.get("successful_records") or 0)
        fail = int(summary.get("failed_records") or 0)
        return ExecutionObservation(
            step_id=step_id,
            tool_id=tool_id,
            status="submitted",
            batch_id=batch_id,
            job_id=job_id,
            mapping_id=mapping_id,
            metrics={
                "batch_status": batch_status,
                "total_records": total,
                "successful_records": ok,
                "failed_records": fail,
                "in_progress": True,
            },
        )

    total = int(summary.get("total_records") or 0)
    ok = int(summary.get("successful_records") or 0)
    fail = int(summary.get("failed_records") or 0)

    if migration_batch_in_progress(
        batch_status=batch_status,
        total_records=total,
        successful_records=ok,
        failed_records=fail,
    ):
        return ExecutionObservation(
            step_id=step_id,
            tool_id=tool_id,
            status="submitted",
            batch_id=batch_id,
            job_id=job_id,
            mapping_id=mapping_id,
            metrics={
                "batch_status": batch_status,
                "total_records": total,
                "successful_records": ok,
                "failed_records": fail,
                "in_progress": True,
            },
        )

    denom = max(total, ok + fail, 1)
    rate = ok / denom

    report = FailureReport(
        batch_id=batch_id,
        destination_type=summary.get("destination_type"),
        destination_object=summary.get("destination_object"),
        success_rate=rate,
        failed_count=fail,
        total_records=denom,
        successful_records=ok,
        top_errors=[
            TopError(
                message=str(item.get("message") or ""),
                count=int(item.get("count") or 1),
            )
            for item in (summary.get("top_errors") or [])
            if isinstance(item, dict) and item.get("message")
        ],
        failed_fields=summary.get("failed_fields") or [],
        error_samples=summary.get("error_samples") or [],
    )
    return ExecutionObservation(
        step_id=step_id,
        tool_id=tool_id,
        status=observation_status_from_rate(rate),  # type: ignore[arg-type]
        batch_id=batch_id,
        job_id=job_id,
        mapping_id=mapping_id,
        metrics={
            "batch_status": batch_status,
            "total_records": denom,
            "successful_records": ok,
            "failed_records": fail,
            "success_rate": rate,
        },
        failure_report=report,
    )


async def observe_step_result(
    session: AgentSession,
    tool_id: str,
    result: ToolResult,
    *,
    step_id: Optional[str] = None,
) -> ExecutionObservation:
    if result.observation:
        obs = result.observation
    elif tool_id == "start_migration" and (
        result.background_job_id
        or (result.user_visible or {}).get("action") == "migration_started"
    ):
        visible = result.user_visible or {}
        obs = ExecutionObservation(
            step_id=step_id,
            tool_id=tool_id,
            status="submitted",
            batch_id=str(visible["batch_id"]) if visible.get("batch_id") else None,
            job_id=str(result.background_job_id or visible.get("job_id") or session.active_job_id or "")
            or None,
            mapping_id=visible.get("mapping_id") or session.mapping_id,
            metrics={
                "total_records": visible.get("total_records") or visible.get("record_count"),
                "in_progress": True,
            },
        )
    elif result.user_visible and result.user_visible.get("batch_id"):
        obs = await build_observation_from_batch(
            tool_id=tool_id,
            batch_id=str(result.user_visible["batch_id"]),
            step_id=step_id,
            job_id=(result.user_visible or {}).get("job_id") or session.active_job_id,
            mapping_id=(result.user_visible or {}).get("mapping_id") or session.mapping_id,
        )
    else:
        status = "success" if result.ok else "failed"
        if result.needs_clarification:
            status = "skipped"
        obs = ExecutionObservation(
            step_id=step_id,
            tool_id=tool_id,
            status=status,  # type: ignore[arg-type]
            job_id=session.active_job_id,
            mapping_id=session.mapping_id,
            metrics={"ok": result.ok},
        )

    session.append_execution_observation(obs)
    return obs


def observation_chat_metadata(obs: ExecutionObservation) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "tool_id": obs.tool_id,
        "status": obs.status,
        "metrics": obs.metrics,
    }
    if obs.failure_report:
        payload["failure_report"] = obs.failure_report.model_dump(mode="json")
    return payload


async def persist_execution_observation(
    session: AgentSession,
    observation: ExecutionObservation,
) -> None:
    if not session.conversation_id:
        return
    try:
        from services.chat_service import ChatService

        meta = {
            "event_type": "execution_observation",
            **observation_chat_metadata(observation),
        }
        if observation.batch_id:
            meta["batch_id"] = observation.batch_id
        if observation.job_id:
            meta["job_id"] = observation.job_id
        await ChatService.log_data_event(
            session.conversation_id,
            session.user_id,
            "execution_observation",
            meta,
        )
    except Exception:
        pass
