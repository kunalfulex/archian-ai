from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from agent.plan import ExecutionPlan
from agent.events import (
    agent_plan_ready,
    agent_planning,
    agent_step_completed,
    agent_step_started,
    agent_turn_completed,
    assistant_message,
)
from agent.planner import Planner
from agent.preview_pipeline import build_agent_preview_pipeline
from agent.registry import ToolRegistry, default_registry
from agent.session import AgentSession
from agent.stream_utils import (
    LIVE_SSE_STREAMING_TOOL_IDS,
    apply_sse_meta_from_line,
    is_raw_handler_clarification_sse_line,
    resolve_tool_stream_handler,
    session_delta_from_sse_meta,
)
from handlers.classification_handler import create_intent_state
from services.chat_service import ChatService

logger = logging.getLogger(__name__)

MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS_PER_TURN", "5"))

_ASSISTANT_TEXT_TOOL_IDS = frozenset(
    {
        "general_ai_response",
        "salesforce_info_request",
        "sql_table_info",
        "google_sheets_info",
    }
)


def _approved_plan_extract_pause(session: AgentSession) -> bool:
    """Approved plan execution should emit planner_plan_execution_clarification, not raw SSE."""
    snap = session.classification_snapshot or {}
    if snap.get("plan_status") != "approved":
        return False
    return bool(snap.get("plan_id") or snap.get("plan_thread_active"))


class AgentRuntime:
    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or default_registry()
        self.planner = Planner()

    async def run_stream(
        self,
        prompt: str,
        session: AgentSession,
        *,
        data_quality_report: Optional[Dict[str, Any]] = None,
    ) -> AsyncIterator[str]:
        from agent.debug import log_agent_runtime

        yield agent_planning()
        plan = await self.planner.build_plan(prompt, session)
        plan = self._sanitize_plan_for_continuation_tokens(plan, session)
        log_agent_runtime(
            "runtime.plan_built",
            {
                "reasoning": plan.reasoning,
                "mode": session.mode,
                "step_count": len(plan.steps),
                "tools": [s.tool_id for s in plan.steps],
                "active_job_id": session.active_job_id,
                "plan_status": (session.classification_snapshot or {}).get("plan_status"),
                "continuation_steps": (session.classification_snapshot or {}).get(
                    "_continuation_steps"
                )
                or (session.classification_snapshot or {}).get("_pipeline_tokens"),
            },
        )
        from services.planning.map_flow_debug import classification_map_pins, log_map_flow

        log_map_flow(
            "runtime_plan_built",
            classification_pins=classification_map_pins(session.classification_snapshot or {}),
            active_job_id=session.active_job_id,
            step_tools=[s.tool_id for s in (plan.steps or [])],
            destination=session.destination,
            destination_name=session.destination_name,
        )
        if self._continuation_requires_job(plan, session):
            from services.planning.map_flow_debug import log_map_flow

            log_map_flow(
                "runtime_continuation_requires_job_abort",
                continuation_steps=(session.classification_snapshot or {}).get(
                    "_continuation_steps"
                )
                or (session.classification_snapshot or {}).get("_pipeline_tokens"),
                active_job_id=session.active_job_id,
                step_tools=[s.tool_id for s in (plan.steps or [])],
            )
            yield f"data: {json.dumps({'status': 'clarification', 'message': 'No active extraction job found. Please run extraction first or ask to extract again.'})}\n\n"
            return

        is_general_turn = session.mode == "general" or (
            plan.reasoning or ""
        ).startswith("general:")
        if is_general_turn and plan.steps:
            tool_id = plan.steps[0].tool_id
            label = plan.steps[0].label or "Assistant reply"
            yield agent_plan_ready(
                plan.reasoning or "general",
                user_label=label,
                pipeline=[{"id": tool_id, "label": label}],
            )
        else:
            pipeline = build_agent_preview_pipeline(plan, session)
            user_label = plan.steps[0].label if plan.steps else None
            if not user_label and pipeline:
                for step in pipeline:
                    sid = (step.get("id") or "").strip()
                    if sid not in ("understand_request", "identify_route"):
                        user_label = step.get("label") or user_label
                        break
            if user_label and (plan.reasoning or "").startswith("architect:"):
                yield agent_plan_ready(
                    plan.reasoning or "",
                    user_label=user_label,
                    pipeline=pipeline,
                )
            else:
                summary = user_label or plan.reasoning or ""
                yield agent_plan_ready(summary, user_label=user_label, pipeline=pipeline)

        from services.planning.plan_runner import refresh_step_arguments

        snap = session.classification_snapshot or {}
        if snap.get("plan_id") and session.conversation_id:
            try:
                from services.chat_service import ChatService
                from services.planning.chat_metadata import plan_execution_checkpoint

                await ChatService.log_data_event(
                    session.conversation_id,
                    session.user_id,
                    "plan_execution_started",
                    plan_execution_checkpoint(
                        plan_id=snap.get("plan_id"),
                        step_index=0,
                        tool_id="plan",
                        status="running",
                    ),
                )
            except Exception:
                pass

        snap = session.classification_snapshot or {}
        start_index = int(snap.get("plan_resume_step_index") or 0)
        steps_slice = plan.steps[start_index:MAX_STEPS]
        if start_index:
            snap.pop("plan_resume_step_index", None)
            session.classification_snapshot = snap

        from services.planning.plan_execution_gates import (
            bootstrap_execution_state,
            effective_completed_step_ids,
            persist_execution_state,
            plan_step_blocked_reason,
            record_step_completion,
            record_step_skipped,
            snapshot_classification_for_pause,
            sync_execution_state_from_session,
        )

        exec_state = bootstrap_execution_state(session, plan_steps=plan.steps)

        for offset, step in enumerate(steps_slice):
            step_index = start_index + offset
            sync_execution_state_from_session(session, exec_state, plan_steps=plan.steps)
            args = refresh_step_arguments(step.tool_id, dict(step.arguments), session)
            if step.tool_id in ("map_fields", "start_migration"):
                from services.planning.map_flow_debug import log_map_flow

                log_map_flow(
                    "runtime_step_start",
                    step_index=step_index,
                    tool_id=step.tool_id,
                    job_id=args.get("job_id"),
                    destination=args.get("destination"),
                    destination_name=args.get("destination_name"),
                )
            if data_quality_report:
                args["data_quality_report"] = data_quality_report

            if args.get("schema_gate_deploy"):
                missing = (snap.get("schema_gate_missing_fields") or [])
                if not missing:
                    record_step_skipped(step.tool_id, args, state=exec_state)
                    persist_execution_state(session, exec_state)
                    await self._log_plan_step_checkpoint(
                        session,
                        step_index,
                        step.tool_id,
                        "skipped",
                        {"reason": "no_schema_delta"},
                        exec_state=exec_state,
                    )
                    continue

            snap = session.classification_snapshot or {}

            if step.tool_id == "google_sheets_create_from_prompt":
                if snap.get("destination_spreadsheet_id") and not snap.get(
                    "planner_create_spreadsheet"
                ):
                    record_step_skipped(step.tool_id, args, state=exec_state)
                    persist_execution_state(session, exec_state)
                    await self._log_plan_step_checkpoint(
                        session,
                        step_index,
                        step.tool_id,
                        "skipped",
                        {"reason": "existing_workbook_selected"},
                        exec_state=exec_state,
                    )
                    continue

            block_reason = plan_step_blocked_reason(
                step.tool_id,
                args,
                state=exec_state,
                session=session,
                label=step.label,
                plan_steps=plan.steps,
            )
            if block_reason:
                from agent.debug import log_agent_runtime

                log_agent_runtime(
                    "runtime.step_blocked",
                    {
                        "tool_id": step.tool_id,
                        "step_index": step_index,
                        "reason": block_reason,
                        "depends_on": args.get("depends_on"),
                        "step_id": args.get("step_id"),
                        "completed": sorted(exec_state.completed_step_ids),
                        "effective_completed": sorted(
                            effective_completed_step_ids(
                                session, exec_state, plan_steps=plan.steps
                            )
                        ),
                        "destination_spreadsheet_id": (session.classification_snapshot or {}).get(
                            "destination_spreadsheet_id"
                        ),
                        "mapping_id": session.mapping_id
                        or (session.classification_snapshot or {}).get("mapping_id"),
                        "extraction_complete": exec_state.extraction_complete,
                        "active_job_id": session.active_job_id,
                    },
                )
                yield f"data: {json.dumps({'status': 'error', 'message': block_reason})}\n\n"
                return

            if (
                args.get("requires_user_approval")
                and args.get("schema_gate_deploy")
                and not snap.get("schema_deploy_approved")
            ):
                from services.planning.plan_execution import build_plan_execution_pending

                pause_cls = snapshot_classification_for_pause(session, exec_state)
                pending = build_plan_execution_pending(
                    classification=pause_cls,
                    original_prompt=session.prompt,
                    resume_step_index=step_index,
                    schema_proposal={
                        "object_api_name": snap.get("schema_gate_object"),
                        "fields_missing_on_destination": snap.get("schema_gate_missing_fields") or [],
                    },
                    conversation_id=session.conversation_id,
                )
                from agent.events import planner_schema_deploy_required

                yield planner_schema_deploy_required(
                    planner_pending=pending,
                    schema_proposal=pending.get("schema_proposal"),
                )
                if session.conversation_id:
                    try:
                        from services.chat_service import ChatService
                        from services.planning.chat_metadata import build_planner_chat_metadata

                        await ChatService.add_message(
                            session.conversation_id,
                            session.user_id,
                            "assistant",
                            "**Schema deploy approval required** — approve below to deploy missing fields, then mapping will continue.",
                            build_planner_chat_metadata(
                                pause_cls,
                                event_type="planner_schema_deploy_required",
                                planner_pending=pending,
                            ),
                        )
                    except Exception:
                        pass
                return

            log_agent_runtime(
                "runtime.step_start",
                {
                    "tool_id": step.tool_id,
                    "step_index": step_index,
                    "args_keys": sorted(args.keys()),
                    "job_id": args.get("job_id") or session.active_job_id,
                },
            )
            yield agent_step_started(step.tool_id, label=step.label)

            tool = self.registry._tools.get(step.tool_id)
            if tool and step.tool_id in LIVE_SSE_STREAMING_TOOL_IDS and hasattr(
                tool, "stream_handler"
            ):
                handler_stream = await resolve_tool_stream_handler(tool, session, args)
                meta: Dict[str, Any] = {}
                assistant_parts: List[str] = []
                defer_raw_clarification = False
                async for item in handler_stream:
                    line = item if isinstance(item, str) else str(item)
                    if (
                        step.tool_id in _ASSISTANT_TEXT_TOOL_IDS
                        and line
                        and not line.lstrip().startswith("data:")
                    ):
                        assistant_parts.append(line)
                        if not line.endswith("\n"):
                            line = f"{line}\n"
                    apply_sse_meta_from_line(line, meta)
                    if (
                        is_raw_handler_clarification_sse_line(line)
                        and _approved_plan_extract_pause(session)
                    ):
                        continue
                    yield line
                if assistant_parts:
                    yield assistant_message("".join(assistant_parts), complete=True)
                if meta.get("_stream_status") == "error":
                    yield f"data: {json.dumps({'status': 'error', 'message': meta.get('_stream_error_message') or 'Request failed'})}\n\n"
                    return
                if meta.get("_stream_status") == "clarification":
                    clar_msg = meta.get("_stream_error_message") or "More information needed."
                    if _approved_plan_extract_pause(session):
                        from agent.events import planner_plan_execution_clarification
                        from services.planning.plan_execution import build_extract_step_pending

                        pause_cls = snapshot_classification_for_pause(session, exec_state)
                        pending = build_extract_step_pending(
                            classification=pause_cls,
                            original_prompt=(session.prompt or "").strip(),
                            resume_step_index=step_index,
                            clarification_message=clar_msg,
                            conversation_id=session.conversation_id,
                        )
                        yield planner_plan_execution_clarification(
                            message=clar_msg,
                            planner_pending=pending,
                        )
                        if session.conversation_id:
                            try:
                                from services.chat_service import ChatService
                                from services.planning.chat_metadata import build_planner_chat_metadata

                                await ChatService.add_message(
                                    session.conversation_id,
                                    session.user_id,
                                    "assistant",
                                    clar_msg,
                                    build_planner_chat_metadata(
                                        pause_cls,
                                        event_type="planner_plan_execution_clarification",
                                        planner_pending=pending,
                                    ),
                                )
                            except Exception:
                                pass
                        return
                    yield f"data: {json.dumps({'status': 'clarification', 'message': clar_msg})}\n\n"
                    return
                if (
                    step.tool_id in self._EXTRACT_TOOL_IDS
                    and not meta.get("_extraction_complete")
                    and meta.get("_stream_status") not in ("clarification", "error")
                ):
                    snap = session.classification_snapshot or {}
                    ops = [
                        str(o).lower()
                        for o in (snap.get("operations") or snap.get("continuation_steps") or [])
                    ]
                    extract_only = ops == ["extract"] or (
                        "extract" in ops
                        and not any(o in ops for o in ("map", "migrate", "migrate_data"))
                    )
                    err_msg = meta.get("_stream_error_message") or (
                        "Extraction did not complete. Check the error above or try again."
                        if extract_only
                        else "Extraction did not complete. Finish extraction before mapping or migrating."
                    )
                    from utils.salesforce_extract_debug import log_sf_extract

                    log_sf_extract(
                        "plan_extract_incomplete",
                        level="warning",
                        always=True,
                        tool_id=step.tool_id,
                        step_index=step_index,
                        conversation_id=session.conversation_id,
                        plan_id=(session.classification_snapshot or {}).get("plan_id"),
                        plan_status=(session.classification_snapshot or {}).get("plan_status"),
                        job_id=meta.get("job_id"),
                        pending_job_id=meta.get("_pending_job_id"),
                        stream_status=meta.get("_stream_status"),
                        stream_error=meta.get("_stream_error_message"),
                        meta_keys=sorted(k for k in meta.keys() if not str(k).startswith("_")),
                    )
                    yield f"data: {json.dumps({'status': 'error', 'message': err_msg})}\n\n"
                    return
                visible = session_delta_from_sse_meta(meta)
                if meta.get("_extraction_finished_sse_emitted"):
                    session.extraction_finished_sse_emitted = True
                if step.tool_id in self._EXTRACT_TOOL_IDS and visible.get("job_id"):
                    jid = visible["job_id"]
                    if not await self._await_extraction_persisted(session.user_id, str(jid)):
                        msg = f"Extraction job {jid} is not ready yet. Try again in a moment."
                        yield f"data: {json.dumps({'status': 'error', 'message': msg})}\n\n"
                        return
                session.merge_result(visible)
                record_step_completion(
                    step.tool_id,
                    args,
                    state=exec_state,
                    meta=meta,
                )
                persist_execution_state(session, exec_state)
                self._track_multi_source_job(session, step.tool_id, visible)
                self._apply_schema_gate_session_state(session, step.tool_id, visible)
                log_agent_runtime(
                    "runtime.step_done",
                    {"tool_id": step.tool_id, "visible_action": visible.get("action")},
                )
                yield agent_step_completed(step.tool_id, visible)
                await self._log_plan_step_checkpoint(
                    session, step_index, step.tool_id, "done", visible, exec_state=exec_state
                )
                from agent.observe import observe_step_result
                from agent.tools.base import ToolResult

                obs = await observe_step_result(
                    session,
                    step.tool_id,
                    ToolResult(ok=True, user_visible=visible),
                    step_id=str(step_index),
                )
                async for obs_line in self._yield_execution_observation(session, obs):
                    yield obs_line
                continue

            result = await self.registry.invoke(step.tool_id, args, session)
            if step.tool_id == "map_fields":
                from services.planning.map_flow_debug import log_map_flow

                visible_preview = result.user_visible or {}
                log_map_flow(
                    "map_fields_result",
                    ok=result.ok,
                    message=(result.message or "")[:300],
                    visible_action=visible_preview.get("action") if isinstance(visible_preview, dict) else None,
                    needs_clarification=result.needs_clarification,
                )
            if not result.ok:
                status = "clarification" if result.needs_clarification else "error"
                yield f"data: {json.dumps({'status': status, 'message': result.message})}\n\n"
                return
            visible = result.user_visible or result.session_delta or {}
            session.merge_result(visible)
            record_step_completion(
                step.tool_id,
                args,
                state=exec_state,
                meta=visible if isinstance(visible, dict) else None,
            )
            persist_execution_state(session, exec_state)
            assistant_parts: List[str] = []
            if result.sse_chunks:
                for sse_line in result.sse_chunks:
                    if (
                        step.tool_id in _ASSISTANT_TEXT_TOOL_IDS
                        and sse_line
                        and not sse_line.lstrip().startswith("data:")
                    ):
                        assistant_parts.append(sse_line)
                        out_line = sse_line if sse_line.endswith("\n") else f"{sse_line}\n"
                        yield out_line
                    else:
                        yield sse_line
            if assistant_parts:
                yield assistant_message("".join(assistant_parts), complete=True)
            self._track_multi_source_job(session, step.tool_id, visible)
            self._apply_schema_gate_session_state(session, step.tool_id, visible)
            log_agent_runtime(
                "runtime.step_done",
                {
                    "tool_id": step.tool_id,
                    "ok": result.ok,
                    "visible_action": (visible or {}).get("action"),
                    "message": result.message if not result.ok else None,
                },
            )
            yield agent_step_completed(step.tool_id, visible)
            if (
                step.tool_id == "start_migration"
                and result.background_job_id
                and (visible or {}).get("action") == "migration_started"
            ):
                async for mig_line in self._stream_migration_job_sse(
                    session, str(result.background_job_id)
                ):
                    yield mig_line
            await self._log_plan_step_checkpoint(
                session, step_index, step.tool_id, "done", visible, exec_state=exec_state
            )
            from agent.observe import observe_step_result

            obs = await observe_step_result(session, step.tool_id, result, step_id=str(step_index))
            async for obs_line in self._yield_execution_observation(session, obs):
                yield obs_line

        persist_execution_state(session, exec_state)
        recovery_pending = await self._maybe_offer_recovery(session)
        if recovery_pending:
            from agent.events import planner_recovery_required

            proposal = recovery_pending.get("recovery_proposal") or {}
            yield planner_recovery_required(
                planner_pending=recovery_pending,
                recovery_proposal=proposal,
            )
            if session.conversation_id:
                try:
                    from agent.recovery_flow import _recovery_chat_message
                    from services.chat_service import ChatService
                    from services.planning.chat_metadata import build_planner_chat_metadata

                    pause_cls = snapshot_classification_for_pause(session, exec_state)
                    await ChatService.add_message(
                        session.conversation_id,
                        session.user_id,
                        "assistant",
                        _recovery_chat_message(proposal),
                        build_planner_chat_metadata(
                            pause_cls,
                            event_type="planner_recovery_required",
                            planner_pending=recovery_pending,
                            extra={
                                "recovery_proposal": proposal,
                                "show_recovery_approval": bool(proposal.get("requires_write")),
                            },
                        ),
                    )
                except Exception:
                    pass
            return

        snapshot = {
            "active_job_id": session.active_job_id,
            "active_version": session.active_version,
            "mode": session.mode,
        }
        yield agent_turn_completed(snapshot)

        from agent.tool_trace import schedule_tool_trace_capture, steps_from_execution_trace

        schedule_tool_trace_capture(
            session,
            prompt=prompt,
            steps=steps_from_execution_trace(session),
        )

        from agent.terminal_extraction import (
            build_terminal_extraction_finished_sse,
            should_emit_terminal_extraction_finished,
        )

        if (
            should_emit_terminal_extraction_finished(session)
            and session.conversation_id
            and session.last_result
        ):
            await self._log_extraction_completed(session, session.last_result)

        if (
            should_emit_terminal_extraction_finished(session)
            and not session.extraction_finished_sse_emitted
        ):
            try:
                sse = await build_terminal_extraction_finished_sse(session)
                if sse:
                    yield sse
                    session.extraction_finished_sse_emitted = True
            except Exception as exc:
                logger.warning("Agent runtime: extraction_job_finished payload failed: %s", exc)

        # Map/migrate: thin ack only (UI payloads already delivered via agent_step_completed).
        if session.mode in ("map", "migrate"):
            terminal: Dict[str, Any] = {
                "status": "success",
                "job_id": session.active_job_id,
            }
            if isinstance(session.last_result, dict):
                action = session.last_result.get("action")
                if action:
                    terminal["action"] = action
            yield f"data: {json.dumps(terminal)}\n\n"
        elif session.mode == "general":
            yield f"data: {json.dumps({'status': 'success', 'event_type': 'general_chat_completed'})}\n\n"
        # extract / data_ops: agent_turn_completed is the turn terminator — no result envelope.

    def _sanitize_plan_for_continuation_tokens(
        self,
        plan: ExecutionPlan,
        session: AgentSession,
    ) -> ExecutionPlan:
        from services.planning.plan_scope import continuation_should_skip_extract_steps

        snap = session.classification_snapshot or {}
        if not continuation_should_skip_extract_steps(snap):
            return plan
        filtered = [s for s in (plan.steps or []) if s.tool_id not in self._EXTRACT_TOOL_IDS]
        if len(filtered) == len(plan.steps):
            return plan
        return ExecutionPlan(
            mode=plan.mode,
            steps=filtered,
            reasoning=plan.reasoning or "sanitized:continuation",
        )

    def _continuation_requires_job(self, plan: ExecutionPlan, session: AgentSession) -> bool:
        snap = session.classification_snapshot or {}
        from services.planning.plan_scope import continuation_pipeline_tokens

        tokens = continuation_pipeline_tokens(snap)
        if "extract" in tokens:
            return False
        if session.active_job_id:
            return False
        needs_job_tools = {
            "run_data_ops_plan",
            "map_fields",
            "start_migration",
        }
        return any(step.tool_id in needs_job_tools for step in (plan.steps or []))

    _EXTRACT_TOOL_IDS = frozenset(
        {"extract_salesforce", "extract_sql", "extract_google_sheets", "extract_file", "file_reextract"}
    )

    async def _await_extraction_persisted(
        self,
        user_id: str,
        job_id: str,
        *,
        max_wait_sec: float = 45.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """Wait until extraction_results row exists for job_id (SQL/SF/GS async save).

        Empty extractions are valid once persisted; do not require len(extracted_data) > 0.
        """
        import asyncio
        import time

        from services.extraction_results_service import ExtractionResultsService

        def _is_persisted(row: Optional[Dict[str, Any]]) -> bool:
            if not row:
                return False
            data = row.get("extracted_data")
            return isinstance(data, list)

        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            row = await ExtractionResultsService.get_extraction_result(job_id, user_id)
            if _is_persisted(row):
                return True
            await asyncio.sleep(poll_interval)
        row = await ExtractionResultsService.get_extraction_result(job_id, user_id)
        return _is_persisted(row)

    def _track_multi_source_job(
        self,
        session: AgentSession,
        tool_id: str,
        visible: Dict[str, Any],
    ) -> None:
        if tool_id not in self._EXTRACT_TOOL_IDS:
            return
        job_id = visible.get("job_id") or session.active_job_id
        if not job_id:
            return
        snap = dict(session.classification_snapshot or {})
        ids = list(snap.get("multi_source_job_ids") or [])
        if job_id not in ids:
            ids.append(job_id)
        snap["multi_source_job_ids"] = ids
        session.classification_snapshot = snap

    async def _maybe_offer_recovery(self, session: AgentSession) -> Optional[Dict[str, Any]]:
        snap = session.classification_snapshot or {}
        if snap.get("recovery_dismissed") or snap.get("recovery_approved"):
            return None
        from agent.recovery_flow import maybe_offer_recovery_from_session

        try:
            return await maybe_offer_recovery_from_session(session)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning(
                "recovery offer skipped after plan: %s", exc, exc_info=True
            )
            return None

    async def _stream_migration_job_sse(
        self, session: AgentSession, background_job_id: str
    ) -> AsyncIterator[str]:
        """Hold architect stream open with migration progress + terminal chat merge events."""
        from utils.migration_sse_stream import iter_migration_job_sse_lines

        async for line in iter_migration_job_sse_lines(
            background_job_id,
            conversation_id=session.conversation_id,
            user_id=session.user_id,
        ):
            yield line

    async def _yield_execution_observation(
        self, session: AgentSession, observation: Any
    ) -> AsyncIterator[str]:
        dump = (
            observation.model_dump(mode="json")
            if hasattr(observation, "model_dump")
            else dict(observation or {})
        )
        report = dump.get("failure_report")
        if not report or dump.get("tool_id") != "start_migration":
            return
        from agent.events import execution_observation
        from agent.observe import persist_execution_observation
        from agent.recovery_policy import should_recover
        from agent.observation import ExecutionObservation

        obs_model = (
            observation
            if isinstance(observation, ExecutionObservation)
            else ExecutionObservation.model_validate(dump)
        )
        if not should_recover(obs_model):
            return
        yield execution_observation(dump)
        await persist_execution_observation(session, obs_model)

    def _apply_schema_gate_session_state(
        self,
        session: AgentSession,
        tool_id: str,
        visible: Dict[str, Any],
    ) -> None:
        if tool_id != "schema_gate_propose":
            return
        snap = dict(session.classification_snapshot or {})
        missing = visible.get("fields_missing_on_destination")
        if missing is None and visible.get("action") == "schema_gate_proposal":
            missing = visible.get("fields_missing_on_destination") or []
        if missing is not None:
            snap["schema_gate_missing_fields"] = list(missing)
            if not missing:
                snap["schema_deploy_approved"] = True
        if visible.get("object_api_name"):
            snap["schema_gate_object"] = visible.get("object_api_name")
        session.classification_snapshot = snap

    async def _log_plan_step_checkpoint(
        self,
        session: AgentSession,
        step_index: int,
        tool_id: str,
        status: str,
        detail: Optional[Dict[str, Any]] = None,
        *,
        exec_state: Optional[Any] = None,
    ) -> None:
        snap = session.classification_snapshot or {}
        if not snap.get("plan_id") or not session.conversation_id:
            return
        try:
            from services.chat_service import ChatService
            from services.planning.chat_metadata import plan_execution_checkpoint

            checkpoint_detail = dict(detail or {})
            if exec_state is not None:
                checkpoint_detail["_plan_completed_step_ids"] = sorted(
                    exec_state.completed_step_ids
                )
                if session.active_job_id:
                    checkpoint_detail["active_job_id"] = session.active_job_id
                if snap.get("destination_spreadsheet_id"):
                    checkpoint_detail["destination_spreadsheet_id"] = snap.get(
                        "destination_spreadsheet_id"
                    )
                if session.mapping_id:
                    checkpoint_detail["mapping_id"] = session.mapping_id
                if session.mapping_config and isinstance(session.mapping_config, dict):
                    checkpoint_detail["mapping_config"] = session.mapping_config
                if snap.get("operations_applied"):
                    checkpoint_detail["operations_applied"] = snap.get("operations_applied")
                if snap.get("extraction_version") is not None:
                    checkpoint_detail["extraction_version"] = snap.get("extraction_version")

            await ChatService.log_data_event(
                session.conversation_id,
                session.user_id,
                "plan_step_checkpoint",
                plan_execution_checkpoint(
                    plan_id=snap.get("plan_id"),
                    step_index=step_index,
                    tool_id=tool_id,
                    status=status,
                    detail=checkpoint_detail,
                ),
            )
        except Exception as exc:
            logger.debug("plan_step_checkpoint log skipped: %s", exc)

    async def _log_extraction_completed(
        self, session: AgentSession, result: Dict[str, Any]
    ) -> None:
        try:
            intent_state = create_intent_state(
                active_intent="extract",
                active_source=session.source or "unknown",
                active_destination=session.destination or "unknown",
                source_name=session.source_name,
                destination_name=session.destination_name,
                job_id=result.get("job_id"),
                state="extraction_completed",
            )
            intent_state["active_version"] = result.get("extraction_version")
            intent_state["extraction_rules"] = result.get("extraction_rules")
            intent_state["active_job_id"] = result.get("job_id")

            # Persist full post-extract metadata (pipeline_narrative, source_schema, rules, …).
            from services.intent.extraction_metadata import build_persisted_extraction_metadata

            snap = session.classification_snapshot or {}
            plan_meta = {}
            exec_plan = session.execution_plan
            if isinstance(exec_plan, dict):
                plan_meta = exec_plan.get("metadata") or {}
            ext_meta = build_persisted_extraction_metadata(
                migration_intent=snap.get("migration_intent") or plan_meta.get("migration_intent"),
                extract_field_scope=result.get("extract_field_scope")
                if isinstance(result.get("extract_field_scope"), dict)
                else None,
                classification=snap,
                source_entity_key=result.get("object_api_name") or result.get("document_type"),
                field_hints=result.get("fields"),
                destination=session.destination,
                destination_name=session.destination_name,
                extra=result.get("extraction_metadata") if isinstance(result.get("extraction_metadata"), dict) else None,
            )
            event_data = {**(result or {}), "intent_state": intent_state, "status": "success", **ext_meta}
            if session.destination:
                event_data["destination"] = session.destination
            if session.destination_name:
                event_data["destination_name"] = session.destination_name
            event_data.setdefault("source_type", session.source)
            event_data.setdefault("source_name", session.source_name)

            from utils.document_category_match import enrich_source_extraction_metadata

            category_meta = await enrich_source_extraction_metadata(
                session.user_id,
                session.destination,
                session.destination_name,
                event_data.get("document_type"),
                event_data.get("document_description"),
                event_data.get("data"),
                source_type=session.source or event_data.get("source_type"),
                source_name=session.source_name or event_data.get("source_name"),
                extraction_metadata=event_data.get("extraction_metadata"),
            )
            event_data.update(category_meta)
            src_type = category_meta.get("source_document_type") or category_meta.get("canonical_document_type")
            src_desc = category_meta.get("source_document_description") or category_meta.get("canonical_document_description")
            if src_type:
                event_data["document_type"] = src_type
                event_data["source_document_type"] = src_type
            if src_desc:
                event_data["document_description"] = src_desc
                event_data["source_document_description"] = src_desc

            await ChatService.log_data_event(
                conversation_id=session.conversation_id,
                user_id=session.user_id,
                event_type="extraction_completed",
                event_data=event_data,
            )
            try:
                from services.conversation.session_sync import sync_session_after_extraction

                await sync_session_after_extraction(
                    session.user_id,
                    session.conversation_id,
                    job_id=str(result.get("job_id") or ""),
                    intent_state=intent_state,
                )
            except Exception as sync_exc:
                logger.warning("Failed to sync conversation session: %s", sync_exc)
        except Exception as exc:
            logger.warning("Failed to log extraction_completed from agent: %s", exc)


async def run_agent_architect_stream(
    prompt: str,
    user_id: str,
    conversation_history: List[Dict],
    conversation_id: Optional[str],
    classification: Dict[str, Any],
    *,
    data_quality_report: Optional[Dict[str, Any]] = None,
    pending_file_upload: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    """Architect funnel through AgentRuntime (extract/migrate/general/info/tools)."""
    session = await AgentSession.from_architect_context(
        user_id,
        conversation_id,
        conversation_history,
        classification,
        prompt,
        pending_file_upload=pending_file_upload,
    )
    runtime = AgentRuntime()
    async for chunk in runtime.run_stream(
        prompt, session, data_quality_report=data_quality_report
    ):
        yield chunk


async def run_agent_data_ops_stream(
    prompt: str,
    user_id: str,
    conversation_history: List[Dict],
    conversation_id: Optional[str],
    classification: Dict[str, Any],
    *,
    data_quality_report: Optional[Dict[str, Any]] = None,
) -> AsyncIterator[str]:
    session = await AgentSession.from_context(
        user_id, conversation_id, conversation_history, classification, prompt
    )
    runtime = AgentRuntime()
    async for chunk in runtime.run_stream(
        prompt, session, data_quality_report=data_quality_report
    ):
        yield chunk
