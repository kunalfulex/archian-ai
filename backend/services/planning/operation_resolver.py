"""Map pipeline operations to connector-specific tool_ids."""
from __future__ import annotations

from typing import Dict, Optional

from services.connectors.registry import all_connectors, get_connector
from services.planning.connector_guards import canonicalize_connector

# Connector-agnostic operations; connector-owned ones come from the connector registry.
_CORE_OPERATION_TOOLS = {
    "map": "map_fields",
    "migrate": "start_migration",
    "transform": "run_data_ops_plan",
    "data_ops": "run_data_ops_plan",
    "load_flow": "load_flow",
    "save_flow": "save_flow",
    "assess_schema": "schema_gate_assess",
    "propose_schema": "schema_gate_propose",
}


def _operation_tools() -> Dict[str, str]:
    tools = dict(_CORE_OPERATION_TOOLS)
    for spec in all_connectors():
        for op, tool_id in spec.operation_tools.items():
            tools.setdefault(op, tool_id)
    return tools


_UNIVERSAL_OPS = frozenset({"map", "migrate", "data_ops", "transform", "load_flow", "save_flow"})


def normalize_operation(operation: str) -> str:
    """Canonical pipeline token (deploy → deploy_schema for internal consistency)."""
    key = (operation or "").strip().lower()
    if key == "deploy":
        return "deploy_schema"
    return key


def resolve_tool_id(
    operation: str,
    connector_type: Optional[str] = None,
    *,
    role: str = "source",
) -> str:
    """
    Resolve a pipeline operation + connector to an AgentRuntime tool_id.

    role is reserved for future source vs destination routing (e.g. create on dest).
    """
    del role  # v1: create always maps to GSheets tool; connector checked via supports_operation
    op = normalize_operation(operation)
    if op == "extract":
        spec = get_connector(canonicalize_connector(connector_type or "salesforce"))
        return spec.extract_tool if spec and spec.extract_tool else "extract_salesforce"
    return _operation_tools().get(op, "general_ai_response")


def supports_operation(connector_type: Optional[str], operation: str) -> bool:
    """True when connector supports this operation in the capability matrix."""
    op = normalize_operation(operation)
    if op in _UNIVERSAL_OPS:
        return True
    ct = canonicalize_connector(connector_type or "")
    if ct == "unknown":
        return op in _operation_tools()
    spec = get_connector(ct)
    if spec is None:
        return False
    if op == "extract":
        return spec.extract_tool is not None
    return op in spec.operations or op.replace("deploy_schema", "deploy") in spec.operations
