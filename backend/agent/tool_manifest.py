"""Mode-scoped tool manifest for dynamic runtime LLM picking."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from agent.registry import ToolRegistry
from agent.tool_spec import spec_for_tool
from agent.tools.base import Tool

DATA_OPS_ALLOWLIST = frozenset({
    "run_data_ops_plan",
    "retrieve_similar_pipelines",
    "map_fields",
    "general_ai_response",
})

RECOVERY_ALLOWLIST = frozenset({
    "analyze_migration_failures",
    "retrieve_similar_pipelines",
    "propose_mapping_remediation",
    "map_fields",
    "start_migration",
})

WRITE_TOOL_IDS = frozenset({
    "map_fields",
    "start_migration",
    "metadata_deploy_salesforce",
    "gsheets_execute_operation",
})

TOOL_PRECONDITIONS: Dict[str, List[str]] = {
    "run_data_ops_plan": ["requires_active_job_id"],
    "map_fields": ["requires_active_job_id", "requires_destination"],
    "retrieve_similar_pipelines": [],
    "general_ai_response": [],
    "analyze_migration_failures": ["requires_batch_id"],
    "propose_mapping_remediation": ["requires_batch_id"],
    "start_migration": ["requires_active_job_id", "requires_mapping", "requires_destination"],
    "metadata_deploy_salesforce": ["requires_schema_deploy_when_pending"],
    "schema_gate_propose": ["requires_schema_deploy_when_pending"],
}


@dataclass
class ToolManifestEntry:
    tool_id: str
    description: str
    allowed_modes: frozenset[str]
    preconditions: List[str] = field(default_factory=list)
    is_write: bool = False
    risk_tier: str = "read"
    parameters_schema: dict = field(default_factory=dict)


def _parameters_schema(tool: Tool) -> dict:
    """Narrow JSON-schema hints for LLM tool arguments."""
    tid = tool.tool_id
    if tid == "run_data_ops_plan":
        return {
            "type": "object",
            "properties": {
                "parent_job_id": {"type": "string"},
                "steps": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "operation_id": {
                                "type": "string",
                                "enum": ["transform", "filter", "dedupe", "validate"],
                            },
                            "prompt": {"type": "string"},
                        },
                    },
                },
            },
        }
    if tid == "retrieve_similar_pipelines":
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "stage": {"type": "string"},
                "chunk_type": {"type": "string"},
            },
        }
    if tid == "map_fields":
        return {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "mapping_context_summary": {"type": "string"},
            },
        }
    if tid == "general_ai_response":
        return {"type": "object", "properties": {"prompt": {"type": "string"}}}
    return {"type": "object", "properties": {}}


def build_manifest(registry: ToolRegistry) -> Dict[str, ToolManifestEntry]:
    out: Dict[str, ToolManifestEntry] = {}
    for tool in registry._tools.values():
        ts = spec_for_tool(tool, parameters_schema=_parameters_schema(tool))
        out[tool.tool_id] = ToolManifestEntry(
            tool_id=tool.tool_id,
            description=tool.description,
            allowed_modes=frozenset(tool.allowed_modes),
            preconditions=list(ts.preconditions),
            is_write=ts.risk_tier in ("write", "deploy"),
            risk_tier=ts.risk_tier,
            parameters_schema=ts.parameters_schema,
        )
    return out


def manifest_for_mode(
    registry: ToolRegistry,
    mode: str,
    *,
    allowlist: Optional[Set[str]] = None,
) -> List[ToolManifestEntry]:
    manifest = build_manifest(registry)
    entries: List[ToolManifestEntry] = []
    for tool in registry.list_for_mode(mode):
        if allowlist and tool.tool_id not in allowlist:
            continue
        entry = manifest.get(tool.tool_id)
        if entry:
            entries.append(entry)
    return entries


def manifest_all_tools(registry: ToolRegistry) -> List[ToolManifestEntry]:
    """Full registry manifest (no mode filter)."""
    manifest = build_manifest(registry)
    return sorted(manifest.values(), key=lambda e: e.tool_id)


def format_manifest_for_llm(
    mode: str,
    entries: List[ToolManifestEntry],
    *,
    max_chars: int = 4000,
) -> str:
    lines = [f"Mode: {mode}", "Available tools:"]
    for entry in entries:
        pre = ", ".join(entry.preconditions) if entry.preconditions else "none"
        tier_flag = f" [{entry.risk_tier.upper()}]" if entry.risk_tier != "read" else ""
        write_flag = " [WRITE]" if entry.is_write else ""
        lines.append(
            f"- {entry.tool_id}{tier_flag}{write_flag}: {entry.description} "
            f"(preconditions: {pre})"
        )
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
