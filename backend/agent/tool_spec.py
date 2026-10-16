"""Self-describing tool specs for Agent Runtime v4 (risk tiers + preconditions)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Literal, Optional, Set

from agent.tools.base import Tool

RiskTier = Literal["read", "transform", "write", "deploy"]


class RiskTierEnum(str, Enum):
    READ = "read"
    TRANSFORM = "transform"
    WRITE = "write"
    DEPLOY = "deploy"


@dataclass
class ToolSpec:
    tool_id: str
    description: str
    parameters_schema: dict = field(default_factory=dict)
    preconditions: List[str] = field(default_factory=list)
    risk_tier: RiskTier = "read"
    produces: List[str] = field(default_factory=list)
    consumes: List[str] = field(default_factory=list)
    streaming: bool = False
    allowed_modes: Set[str] = field(default_factory=set)

    @property
    def requires_approval(self) -> bool:
        return self.risk_tier in ("write", "deploy")


# Per-tool overrides. Tools not listed inherit tier from prefix/heuristic in spec_for_tool().
TOOL_SPEC_OVERRIDES: Dict[str, Dict] = {
    "run_data_ops_plan": {
        "risk_tier": "transform",
        "preconditions": ["requires_active_job_id"],
        "consumes": ["job_id"],
        "produces": ["job_id", "extraction_version"],
        "streaming": True,
    },
    "retrieve_similar_pipelines": {"risk_tier": "read", "preconditions": []},
    "map_fields": {
        "risk_tier": "write",
        "preconditions": ["requires_active_job_id", "requires_destination"],
        "consumes": ["job_id"],
        "produces": ["mapping_id", "mapping_config"],
    },
    "start_migration": {
        "risk_tier": "write",
        "preconditions": ["requires_active_job_id", "requires_mapping", "requires_destination"],
        "consumes": ["job_id", "mapping_id"],
        "produces": ["batch_id"],
    },
    "general_ai_response": {"risk_tier": "read", "streaming": True},
    "analyze_migration_failures": {
        "risk_tier": "read",
        "preconditions": ["requires_batch_id"],
    },
    "propose_mapping_remediation": {
        "risk_tier": "read",
        "preconditions": ["requires_batch_id"],
    },
    "metadata_deploy_salesforce": {
        "risk_tier": "deploy",
        "preconditions": ["requires_schema_deploy_when_pending"],
    },
    "schema_gate_assess": {"risk_tier": "read", "preconditions": []},
    "schema_gate_propose": {
        "risk_tier": "deploy",
        "preconditions": ["requires_schema_deploy_when_pending"],
    },
    "gsheets_execute_operation": {"risk_tier": "write", "preconditions": []},
    "save_flow": {"risk_tier": "write", "preconditions": []},
    "load_flow": {"risk_tier": "read", "preconditions": []},
    "file_reextract": {
        "risk_tier": "transform",
        "preconditions": ["requires_active_job_id"],
        "streaming": True,
    },
    "extract_google_sheets": {
        "risk_tier": "transform",
        "produces": ["job_id"],
        "streaming": True,
    },
    "extract_sql": {"risk_tier": "transform", "produces": ["job_id"], "streaming": True},
    "extract_salesforce": {
        "risk_tier": "transform",
        "produces": ["job_id"],
        "streaming": True,
    },
    "extract_file": {"risk_tier": "transform", "produces": ["job_id"], "streaming": True},
    "google_sheets_create_from_prompt": {
        "risk_tier": "write",
        "streaming": True,
        "produces": ["spreadsheet_id"],
    },
    "google_sheets_update_metadata": {"risk_tier": "transform"},
    "google_sheets_info": {"risk_tier": "read", "streaming": True},
    "sql_table_info": {"risk_tier": "read", "streaming": True},
    "salesforce_info_request": {"risk_tier": "read", "streaming": True},
}


def _default_tier_for_tool_id(tool_id: str) -> RiskTier:
    if tool_id.startswith("extract_") or tool_id.endswith("_reextract"):
        return "transform"
    if tool_id.endswith("_info") or tool_id.endswith("_info_request"):
        return "read"
    return "read"


def spec_for_tool(tool: Tool, *, parameters_schema: Optional[dict] = None) -> ToolSpec:
    """Build ToolSpec from a registered tool + central overrides."""
    override = dict(TOOL_SPEC_OVERRIDES.get(tool.tool_id) or {})
    tier: RiskTier = override.get("risk_tier") or _default_tier_for_tool_id(tool.tool_id)
    streaming = bool(
        override.get("streaming")
        or tool.tool_id in {
            "run_data_ops_plan",
            "general_ai_response",
            "extract_google_sheets",
            "extract_sql",
            "extract_salesforce",
            "extract_file",
            "file_reextract",
            "google_sheets_create_from_prompt",
            "google_sheets_info",
            "sql_table_info",
            "salesforce_info_request",
        }
    )
    return ToolSpec(
        tool_id=tool.tool_id,
        description=tool.description,
        parameters_schema=parameters_schema or {"type": "object", "properties": {}},
        preconditions=list(override.get("preconditions") or []),
        risk_tier=tier,
        produces=list(override.get("produces") or []),
        consumes=list(override.get("consumes") or []),
        streaming=streaming,
        allowed_modes=set(tool.allowed_modes),
    )


def preconditions_for_tool_id(tool_id: str) -> List[str]:
    override = TOOL_SPEC_OVERRIDES.get(tool_id) or {}
    return list(override.get("preconditions") or [])
