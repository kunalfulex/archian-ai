"""Connector registry: one place that lists each connector and what it supports.

Built-in connectors register below. Extra connectors (e.g. from archian-cloud) can
call ``register_connector`` or expose an ``archian.connectors`` entry point whose
target is a zero-argument callable that registers them.
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "archian.connectors"

AdapterFactory = Callable[[str], Any]


def lazy_adapter(module: str, class_name: str) -> AdapterFactory:
    """Factory that imports ``module.class_name`` on first use and builds it with ``user_id``."""

    def _factory(user_id: str) -> Any:
        return getattr(importlib.import_module(module), class_name)(user_id)

    _factory.__qualname__ = f"lazy_adapter({module}.{class_name})"
    return _factory


@dataclass(frozen=True)
class ConnectorSpec:
    connector_type: str
    display_name: str
    aliases: Tuple[str, ...] = ()
    extract_tool: Optional[str] = None
    # Connector-specific pipeline operations (capability matrix), e.g. "deploy", "join".
    operations: FrozenSet[str] = frozenset()
    # Operation -> AgentRuntime tool_id for operations this connector owns.
    operation_tools: Mapping[str, str] = field(default_factory=dict)
    metadata_adapter: Optional[AdapterFactory] = None
    assessment_adapter: Optional[AdapterFactory] = None


_REGISTRY: Dict[str, ConnectorSpec] = {}
_plugins_loaded = False


def register_connector(spec: ConnectorSpec, *, replace: bool = False) -> None:
    key = spec.connector_type
    if not key or key != key.strip().lower():
        raise ValueError(f"connector_type must be a non-empty lowercase id: {key!r}")
    if key in _REGISTRY and not replace:
        raise ValueError(f"Connector already registered: {key}")
    _REGISTRY[key] = spec


def _load_plugins() -> None:
    global _plugins_loaded
    if _plugins_loaded:
        return
    _plugins_loaded = True
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        try:
            ep.load()()
        except Exception:
            logger.exception("Failed to load connector plugin %s", ep.name)


def get_connector(connector_type: Optional[str]) -> Optional[ConnectorSpec]:
    """Exact lookup by canonical type (no alias or case normalization)."""
    _load_plugins()
    return _REGISTRY.get(connector_type or "")


def all_connectors() -> List[ConnectorSpec]:
    _load_plugins()
    return list(_REGISTRY.values())


def connector_types() -> FrozenSet[str]:
    _load_plugins()
    return frozenset(_REGISTRY)


def connector_aliases() -> Dict[str, str]:
    """Alias -> canonical connector type, across all registered connectors."""
    _load_plugins()
    return {alias: spec.connector_type for spec in _REGISTRY.values() for alias in spec.aliases}


register_connector(
    ConnectorSpec(
        connector_type="salesforce",
        display_name="Salesforce",
        aliases=("sf", "sfdc"),
        extract_tool="extract_salesforce",
        operations=frozenset({"extract", "deploy", "deploy_schema", "map", "migrate"}),
        operation_tools={
            "deploy": "metadata_deploy_salesforce",
            "deploy_schema": "metadata_deploy_salesforce",
        },
        metadata_adapter=lazy_adapter(
            "services.connector_metadata.adapters.salesforce_adapter", "SalesforceMetadataAdapter"
        ),
        assessment_adapter=lazy_adapter(
            "services.data_assessment.adapters.salesforce_adapter", "SalesforceAssessmentAdapter"
        ),
    )
)

register_connector(
    ConnectorSpec(
        connector_type="sql",
        display_name="Database",
        aliases=("database", "db"),
        extract_tool="extract_sql",
        operations=frozenset({"extract", "map", "migrate"}),
        metadata_adapter=lazy_adapter(
            "services.connector_metadata.adapters.sql_adapter", "SqlMetadataAdapter"
        ),
        assessment_adapter=lazy_adapter(
            "services.data_assessment.adapters.sql_adapter", "SqlAssessmentAdapter"
        ),
    )
)

register_connector(
    ConnectorSpec(
        connector_type="google_sheets",
        display_name="Google Sheets",
        aliases=("gsheets", "spreadsheet", "workbook", "sheet"),
        extract_tool="extract_google_sheets",
        operations=frozenset(
            {"extract", "create", "join", "upsert", "update", "search", "formula", "map", "migrate"}
        ),
        operation_tools={
            "create": "google_sheets_create_from_prompt",
            "create_spreadsheet": "google_sheets_create_from_prompt",
            "join": "gsheets_execute_operation",
            "upsert": "gsheets_execute_operation",
            "update": "google_sheets_update_metadata",
            "search": "gsheets_execute_operation",
            "formula": "gsheets_execute_operation",
        },
        metadata_adapter=lazy_adapter(
            "services.connector_metadata.adapters.google_sheets_adapter", "GoogleSheetsMetadataAdapter"
        ),
        assessment_adapter=lazy_adapter(
            "services.data_assessment.adapters.google_sheets_adapter", "GoogleSheetsAssessmentAdapter"
        ),
    )
)

register_connector(
    ConnectorSpec(
        connector_type="file",
        display_name="File upload",
        extract_tool="extract_file",
        operations=frozenset({"extract", "map", "migrate"}),
    )
)
