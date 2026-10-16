from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterable, Dict, List, Set

from agent.events import agent_operation_completed, agent_operation_started
from agent.session import AgentSession
from agent.tools.base import Tool, ToolResult
from post_extract.orchestrator import PostExtractOrchestrator
from post_extract.session import OperationPlan, OperationStep
from utils.extraction_sse_payload import build_job_status_after_db_save, extraction_job_finished_sse
from utils.operation_labels import data_ops_phase_id, label_for_operation


def _build_steps(args: Dict[str, Any], session: AgentSession) -> List[OperationStep]:
    steps_spec = args.get("steps") or []
    if not steps_spec and session.operations:
        steps_spec = [
            {"operation_id": op, "prompt": session.prompt}
            for op in session.operations
        ]
    steps: List[OperationStep] = []
    for s in steps_spec:
        if isinstance(s, dict):
            operation_id = s.get("operation_id") or s.get("tool_id")
            if not operation_id:
                continue
            steps.append(
                OperationStep(
                    operation_id=operation_id,
                    prompt=s.get("prompt") or session.prompt,
                    params=s.get("params") or {},
                )
            )
        elif isinstance(s, str) and s.strip():
            steps.append(OperationStep(operation_id=s.strip(), prompt=session.prompt))
    return steps


class RunDataOpsPlanTool(Tool):
    tool_id = "run_data_ops_plan"
    description = "Apply post-extract transform/filter/dedupe/validate on stored extraction data"
    allowed_modes: Set[str] = {"data_ops", "extract", "map", "migrate"}

    async def _run_orchestrator(
        self,
        session: AgentSession,
        args: Dict[str, Any],
        on_operation_event,
    ) -> Dict[str, Any]:
        job_id = args.get("parent_job_id") or session.active_job_id
        if not job_id:
            raise ValueError("No active extraction job_id")

        steps = _build_steps(args, session)
        plan = OperationPlan(parent_job_id=job_id, steps=steps)
        return await PostExtractOrchestrator().run(
            plan,
            session.user_id,
            conversation_id=session.conversation_id,
            extraction_rules_override=session.extraction_rules,
            data_quality_report=args.get("data_quality_report"),
            on_operation_event=on_operation_event,
        )

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[str]:
        """Yield agent_operation_* SSE live as each post-extract plugin runs."""
        queue: asyncio.Queue = asyncio.Queue()
        sentinel = object()

        async def on_operation_event(kind: str, op_id: str, meta: Dict[str, Any]) -> None:
            label = label_for_operation(op_id)
            phase_id = data_ops_phase_id(op_id)
            if kind == "started":
                await queue.put(
                    agent_operation_started(
                        op_id,
                        label=label,
                        phase_id=phase_id,
                    )
                )
            elif kind == "completed":
                await queue.put(
                    agent_operation_completed(
                        op_id,
                        {
                            "label": label,
                            "detail": meta.get("summary") or meta.get("detail"),
                            "rules_updated": meta.get("rules_updated"),
                        },
                        phase_id=phase_id,
                    )
                )

        async def worker() -> None:
            try:
                result = await self._run_orchestrator(session, args, on_operation_event)
                nj = result.get("job_id")
                if nj:
                    try:
                        cls_meta = {
                            "source": session.source,
                            "destination": session.destination,
                            "source_name": session.source_name,
                            "destination_name": session.destination_name,
                        }
                        js = await build_job_status_after_db_save(
                            job_id=nj,
                            user_id=session.user_id,
                            ops_meta=dict(result),
                            classification=cls_meta,
                        )
                        await queue.put(extraction_job_finished_sse(nj, js))
                    except Exception:
                        pass
            except Exception as exc:
                await queue.put(
                    f"data: {json.dumps({'status': 'error', 'message': str(exc)})}\n\n"
                )
            finally:
                await queue.put(sentinel)

        task = asyncio.create_task(worker())
        try:
            while True:
                item = await queue.get()
                if item is sentinel:
                    break
                yield item
        finally:
            if not task.done():
                task.cancel()

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        sse_chunks: List[str] = []

        async def on_operation_event(kind: str, op_id: str, meta: Dict[str, Any]) -> None:
            label = label_for_operation(op_id)
            phase_id = data_ops_phase_id(op_id)
            if kind == "started":
                sse_chunks.append(
                    agent_operation_started(op_id, label=label, phase_id=phase_id)
                )
            elif kind == "completed":
                sse_chunks.append(
                    agent_operation_completed(
                        op_id,
                        {
                            "label": label,
                            "detail": meta.get("summary") or meta.get("detail"),
                            "rules_updated": meta.get("rules_updated"),
                        },
                        phase_id=phase_id,
                    )
                )

        try:
            result = await self._run_orchestrator(session, args, on_operation_event)
        except ValueError as exc:
            return ToolResult(ok=False, message=str(exc))
        child_job_id = result.get("job_id") if isinstance(result, dict) else None
        if child_job_id and session.conversation_id:
            from services.conversation.session_sync import sync_session_after_post_extract

            await sync_session_after_post_extract(
                session.user_id,
                session.conversation_id,
                job_id=str(child_job_id),
            )
        return ToolResult(
            ok=True,
            message="Post-extract operations completed",
            session_delta=result,
            user_visible=result,
            sse_chunks=sse_chunks or None,
        )
