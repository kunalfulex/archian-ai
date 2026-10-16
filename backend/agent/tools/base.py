from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

from agent.session import AgentSession

if TYPE_CHECKING:
    from agent.tool_spec import ToolSpec


@dataclass
class ToolResult:
    ok: bool
    message: Optional[str] = None
    needs_clarification: bool = False
    session_delta: Optional[Dict[str, Any]] = None
    background_job_id: Optional[str] = None
    user_visible: Optional[Dict[str, Any]] = None
    sse_chunks: Optional[List[str]] = None
    observation: Optional[Any] = None  # ExecutionObservation when set by tool


class Tool(ABC):
    tool_id: str
    description: str
    allowed_modes: Set[str]

    def spec(self) -> "ToolSpec":
        from agent.tool_spec import spec_for_tool

        return spec_for_tool(self)

    @abstractmethod
    async def run(self, session: AgentSession, args: Dict[str, Any]) -> ToolResult:
        ...
