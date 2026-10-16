"""Google Sheets execute tool — runs PLAN_SCOPE operations via executor."""

from __future__ import annotations

from typing import Any, AsyncIterable, Dict

from agent.session import AgentSession
from agent.tools.architect_routes import _consume_handler_stream
from agent.tools.base import Tool, ToolResult
from services.google_sheets.executor import execute_plan_stream
from services.google_sheets.models import OperationPlan, OperationType
from services.google_sheets.response_summary import build_error_response


def _snap(session: AgentSession) -> Dict[str, Any]:
    return dict(session.classification_snapshot or {})


_FORCED_OP_MAP = {
    "upsert": OperationType.UPSERT,
    "search": OperationType.SEARCH,
    "update": OperationType.RENAME,
    "formula": OperationType.FORMULA,
}


class GSheetsExecuteOperationTool(Tool):
    tool_id = "gsheets_execute_operation"
    description = "Execute planned Google Sheets operation"
    allowed_modes = {"extract", "migrate", "general"}

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        snap = _snap(session)
        forced_op = (args.get("operation") or "").strip().lower()
        job_ids = list(snap.get("multi_source_job_ids") or [])
        is_multi_source = bool(snap.get("planner_additional_sources")) or len(job_ids) >= 2

        join_contract = args.get("join_contract") if isinstance(args.get("join_contract"), dict) else {}
        join_key = (snap.get("join_key") or join_contract.get("join_key") or "").strip()
        if join_key:
            snap = {**snap, "join_key": join_key}

        if forced_op in _FORCED_OP_MAP:
            plan = OperationPlan(
                operation=_FORCED_OP_MAP[forced_op],
                confidence=0.95,
            )

            async def generate_forced():
                async for chunk in execute_plan_stream(
                    plan,
                    session.prompt,
                    session.user_id,
                    session.conversation_history,
                    snap,
                    conversation_id=session.conversation_id,
                ):
                    yield chunk

            return generate_forced()

        if forced_op == "join" and is_multi_source and len(job_ids) >= 2:
            from services.planning.multi_source import (
                stream_combine_multi_source_extractions,
            )

            async def generate():
                async for chunk in stream_combine_multi_source_extractions(
                    user_id=session.user_id,
                    conversation_id=session.conversation_id,
                    prompt=session.prompt,
                    classification=snap,
                    job_ids=job_ids,
                ):
                    yield chunk

            return generate()

        if forced_op == "join":

            async def generate_join_wait():
                for chunk in build_error_response(
                    "Combine not ready",
                    "Run the extract steps first, then the combine step will merge sources.",
                    hints=[
                        "Multi-source plans extract each source, then combine on a shared key.",
                        "Classifier must set join_key (e.g. combine on Email).",
                    ],
                ):
                    yield chunk

            return generate_join_wait()

        async def generate_missing_op():
            for chunk in build_error_response(
                "Sheets operation not specified",
                "This step requires a pipeline operation from PLAN_SCOPE (upsert, search, formula).",
                hints=["Re-run planning or approve the proposed plan before executing."],
            ):
                yield chunk

        return generate_missing_op()

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


__all__ = [
    "GSheetsExecuteOperationTool",
]
