"""Agent tool to retrieve similar past pipeline runs."""
from __future__ import annotations

from typing import Any, Dict

from agent.session import AgentSession
from agent.tools.base import Tool, ToolResult
from services.pipeline_learning.service import PipelineLearningService


class RetrieveSimilarPipelinesTool(Tool):
    tool_id = "retrieve_similar_pipelines"
    description = "Retrieve similar past extraction/mapping/migration examples for this user"
    allowed_modes = {"extract", "data_ops", "map", "migrate", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        query = (args.get("query") or session.prompt or "").strip()
        if not query:
            return ToolResult(ok=False, message="Provide a query describing the current task.")
        stage = args.get("stage")
        chunk_type = args.get("chunk_type")
        chunks = await PipelineLearningService.retrieve(
            user_id=session.user_id,
            query_text=query,
            stage=stage,
            chunk_type=chunk_type,
            source_type=args.get("source_type") or session.source,
            destination_type=args.get("destination_type") or session.destination,
            caller=args.get("caller") or "retrieve_tool",
            conversation_id=session.conversation_id,
        )
        context = PipelineLearningService.format_retrieval_context(chunks)
        return ToolResult(
            ok=True,
            user_visible={
                "action": "similar_pipelines",
                "count": len(chunks),
                "learning_context": context,
                "chunks": chunks,
            },
        )
