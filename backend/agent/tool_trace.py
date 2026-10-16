"""Capture tool execution traces for RAG (Agent Runtime v4 Phase 0)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.session import AgentSession

logger = logging.getLogger(__name__)


def steps_from_execution_trace(session: AgentSession) -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = []
    for entry in session.execution_trace or []:
        if not isinstance(entry, dict):
            continue
        tool_id = entry.get("tool_id")
        if not tool_id:
            continue
        status = entry.get("status") or "unknown"
        steps.append(
            {
                "tool_id": str(tool_id),
                "status": status,
                "ok": status in ("success", "submitted"),
            }
        )
    return steps


def steps_from_dynamic_trace(trace: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "tool_id": str(t.get("tool_id") or ""),
            "ok": bool(t.get("ok")),
            "rationale": (t.get("rationale") or "")[:200],
        }
        for t in trace
        if t.get("tool_id")
    ]


def infer_partial(steps: List[Dict[str, Any]], session: AgentSession) -> bool:
    if any(s.get("ok") is False for s in steps):
        return True
    for entry in reversed(session.execution_trace or []):
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in ("partial", "failed"):
            return True
        fr = entry.get("failure_report")
        if isinstance(fr, dict) and fr.get("success_rate", 1.0) < 0.95:
            return True
    return False


def schedule_tool_trace_capture(
    session: AgentSession,
    *,
    prompt: str,
    steps: List[Dict[str, Any]],
    partial: Optional[bool] = None,
) -> None:
    if not steps:
        return
    is_partial = infer_partial(steps, session) if partial is None else partial
    try:
        from services.pipeline_learning.service import PipelineLearningService

        if not PipelineLearningService.enabled():
            return
        PipelineLearningService.schedule_capture(
            lambda: PipelineLearningService.capture_tool_trace(
                user_id=session.user_id,
                conversation_id=session.conversation_id,
                prompt=prompt,
                steps=steps,
                partial=is_partial,
                source_type=session.source,
                destination_type=session.destination,
                job_id=session.active_job_id,
                mode=session.mode,
            )
        )
    except Exception as exc:
        logger.debug("tool trace capture schedule failed: %s", exc)
