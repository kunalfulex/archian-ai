"""Agent tools to load and save migration flows."""
from __future__ import annotations

from typing import Any, Dict

from agent.session import AgentSession
from agent.tools.base import Tool, ToolResult


class LoadFlowTool(Tool):
    tool_id = "load_flow"
    description = "Load a saved migration flow and apply its configuration to the session"
    allowed_modes = {"extract", "migrate", "map", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        flow_id = args.get("flow_id") or (session.classification_snapshot or {}).get("flow_id")
        if not flow_id:
            return ToolResult(ok=False, message="No flow_id provided.", needs_clarification=True)

        from services.planning.flow_steps import apply_saved_flow_to_classification

        merged = await apply_saved_flow_to_classification(
            dict(session.classification_snapshot or {}),
            str(flow_id),
            session.user_id,
        )
        session.classification_snapshot = merged
        session.source = merged.get("source")
        session.destination = merged.get("destination")
        session.source_name = merged.get("source_name")
        session.destination_name = merged.get("destination_name")
        if merged.get("extraction_rules"):
            session.extraction_rules = merged["extraction_rules"]

        name = merged.get("reasoning") or flow_id
        return ToolResult(
            ok=True,
            user_visible={
                "action": "flow_loaded",
                "flow_id": str(flow_id),
                "message": f"Loaded flow configuration ({name}).",
                "classification": merged,
            },
            session_delta={"flow_id": str(flow_id)},
        )


class SaveFlowTool(Tool):
    tool_id = "save_flow"
    description = "Save the current extraction and mapping as a reusable migration flow"
    allowed_modes = {"extract", "migrate", "map", "data_ops", "general"}

    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        flow_name = (args.get("flow_name") or "").strip()
        if not flow_name:
            flow_name = (session.prompt or "Archian flow")[:80].strip() or "Saved flow"

        if not session.conversation_id:
            return ToolResult(
                ok=False,
                message="Save flow requires an active conversation session.",
            )

        job_id = args.get("job_id") or session.active_job_id
        if not job_id:
            return ToolResult(
                ok=False,
                message="Complete an extraction (and mapping if needed) before saving a flow.",
            )

        try:
            from services.conversation.actions.save_flow import run_save_flow_action

            result = await run_save_flow_action(
                user_id=str(session.user_id),
                conversation_id=str(session.conversation_id),
                flow_name=flow_name,
                job_id=str(job_id),
            )
        except ValueError as exc:
            return ToolResult(ok=False, message=str(exc))
        except Exception as exc:
            return ToolResult(ok=False, message=f"Failed to save flow: {exc}")

        flow_id = result.get("flow_id")
        return ToolResult(
            ok=True,
            user_visible={
                "action": "flow_saved",
                "flow_id": flow_id,
                "flow_name": flow_name,
                "message": f"Saved flow **{flow_name}**. You can run it anytime from Flows or say “run flow {flow_name}”.",
            },
        )
