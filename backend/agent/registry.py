from __future__ import annotations

from typing import Dict, List

from agent.session import AgentSession
from agent.tools.architect_routes import (
    ExtractFileTool,
    ExtractGoogleSheetsTool,
    ExtractSalesforceTool,
    ExtractSqlTool,
    FileReextractTool,
    GeneralAiResponseTool,
    GoogleSheetsCreateFromPromptTool,
    GoogleSheetsInfoTool,
    GoogleSheetsUpdateMetadataTool,
    MetadataDeploySalesforceTool,
    SalesforceInfoRequestTool,
    SqlTableInfoTool,
)
from agent.tools.base import Tool, ToolResult
from agent.tools.data_ops import RunDataOpsPlanTool
from agent.tools.flow_tools import LoadFlowTool, SaveFlowTool
from agent.tools.schema_gate_tools import SchemaGateAssessTool, SchemaGateProposeTool
from agent.tools.map_migrate_tools import MapFieldsTool, StartMigrationTool
from agent.tools.pipeline_learning_tools import RetrieveSimilarPipelinesTool
from agent.tools.recovery_tools import AnalyzeMigrationFailuresTool, ProposeMappingRemediationTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.tool_id] = tool

    def list_for_mode(self, mode: str) -> List[Tool]:
        return [t for t in self._tools.values() if mode in t.allowed_modes]

    async def invoke(
        self, tool_id: str, arguments: dict, session: AgentSession
    ) -> ToolResult:
        tool = self._tools.get(tool_id)
        if not tool:
            return ToolResult(ok=False, message=f"Unknown tool: {tool_id}")
        if session.mode not in tool.allowed_modes:
            return ToolResult(
                ok=False,
                message=f"Tool {tool_id} not allowed in mode {session.mode}",
            )
        return await tool.run(session, arguments)


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(RunDataOpsPlanTool())
    reg.register(ExtractGoogleSheetsTool())
    reg.register(ExtractSqlTool())
    reg.register(ExtractSalesforceTool())
    reg.register(MetadataDeploySalesforceTool())
    reg.register(ExtractFileTool())
    reg.register(FileReextractTool())
    reg.register(GoogleSheetsCreateFromPromptTool())
    reg.register(GoogleSheetsUpdateMetadataTool())
    reg.register(GoogleSheetsInfoTool())
    reg.register(SqlTableInfoTool())
    reg.register(SalesforceInfoRequestTool())
    reg.register(GeneralAiResponseTool())
    reg.register(MapFieldsTool())
    reg.register(StartMigrationTool())
    reg.register(LoadFlowTool())
    reg.register(SaveFlowTool())
    reg.register(SchemaGateAssessTool())
    reg.register(SchemaGateProposeTool())
    reg.register(RetrieveSimilarPipelinesTool())
    reg.register(AnalyzeMigrationFailuresTool())
    reg.register(ProposeMappingRemediationTool())
    from agent.tools.google_sheets_micro import GSheetsExecuteOperationTool

    reg.register(GSheetsExecuteOperationTool())
    return reg
