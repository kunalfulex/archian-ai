"""Schema gate tools (assess / propose before map)."""
from __future__ import annotations

from typing import Any, AsyncIterable, Dict

from agent.session import AgentSession
from agent.tools.architect_routes import _consume_handler_stream
from agent.tools.base import Tool, ToolResult
from services.planning.schema_gate_service import stream_schema_gate_phase

_SCHEMA_GATE_MODES = {"extract", "migrate", "map", "data_ops", "general"}


class SchemaGateAssessTool(Tool):
    tool_id = "schema_gate_assess"
    description = "Assess Salesforce destination schema vs source before mapping"
    allowed_modes = _SCHEMA_GATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        snap = session.classification_snapshot or {}
        return stream_schema_gate_phase(
            user_id=session.user_id,
            source_name=args.get("source_name") or snap.get("source_name") or "",
            destination_name=args.get("destination_name") or snap.get("destination_name") or "",
            prompt=session.prompt,
            conversation_history=session.conversation_history,
            gate_phase="assess",
            job_id=session.active_job_id,
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))


class SchemaGateProposeTool(Tool):
    tool_id = "schema_gate_propose"
    description = "Present proposed Salesforce schema changes for user review"
    allowed_modes = _SCHEMA_GATE_MODES

    async def stream_handler(
        self, session: AgentSession, args: Dict[str, Any]
    ) -> AsyncIterable[Any]:
        snap = session.classification_snapshot or {}
        return stream_schema_gate_phase(
            user_id=session.user_id,
            source_name=args.get("source_name") or snap.get("source_name") or "",
            destination_name=args.get("destination_name") or snap.get("destination_name") or "",
            prompt=session.prompt,
            conversation_history=session.conversation_history,
            gate_phase="propose_schema_delta",
            job_id=session.active_job_id,
        )

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        return await _consume_handler_stream(lambda: self.stream_handler(session, args))
