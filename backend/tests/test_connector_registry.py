"""Connector registry: built-ins are unchanged and extra connectors plug in everywhere."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from services.connectors import registry as reg
from services.connectors.registry import ConnectorSpec, get_connector, register_connector
from services.connector_metadata.adapters.registry import get_metadata_adapter
from services.data_assessment.adapters.registry import get_adapter
from services.planning.connector_guards import canonicalize_connector, is_known_connector
from services.planning.multi_source import _normalize_connector_type
from services.planning.operation_resolver import resolve_tool_id, supports_operation


@pytest.fixture
def isolated_registry():
    saved = dict(reg._REGISTRY)
    saved_loaded = reg._plugins_loaded
    try:
        yield
    finally:
        reg._REGISTRY.clear()
        reg._REGISTRY.update(saved)
        reg._plugins_loaded = saved_loaded


def test_builtin_connectors():
    assert reg.connector_types() == frozenset({"salesforce", "sql", "google_sheets", "file"})
    assert get_connector("google_sheets").display_name == "Google Sheets"
    assert get_connector("SALESFORCE") is None


@pytest.mark.parametrize(
    "op, ct, tool",
    [
        ("extract", "salesforce", "extract_salesforce"),
        ("extract", "db", "extract_sql"),
        ("extract", "sheet", "extract_google_sheets"),
        ("extract", "file", "extract_file"),
        ("extract", "hubspot", "extract_salesforce"),
        ("extract", None, "extract_salesforce"),
        ("deploy", None, "metadata_deploy_salesforce"),
        ("create", "sql", "google_sheets_create_from_prompt"),
        ("join", None, "gsheets_execute_operation"),
        ("update", None, "google_sheets_update_metadata"),
        ("map", "sql", "map_fields"),
        ("transform", None, "run_data_ops_plan"),
        ("propose_schema", None, "schema_gate_propose"),
        ("nonsense", "salesforce", "general_ai_response"),
    ],
)
def test_resolve_tool_id_builtins(op, ct, tool):
    assert resolve_tool_id(op, ct) == tool


@pytest.mark.parametrize(
    "ct, op, expected",
    [
        ("salesforce", "deploy", True),
        ("salesforce", "deploy_schema", True),
        ("sql", "deploy", False),
        ("google_sheets", "join", True),
        ("salesforce", "join", False),
        ("file", "extract", True),
        ("file", "create", False),
        ("hubspot", "map", True),
        ("hubspot", "join", True),
        ("hubspot", "extract", False),
        ("", "create_spreadsheet", True),
    ],
)
def test_supports_operation_builtins(ct, op, expected):
    assert supports_operation(ct, op) is expected


def test_adapter_factories_unchanged():
    assert type(get_metadata_adapter("salesforce", "u1")).__name__ == "SalesforceMetadataAdapter"
    assert type(get_adapter("google_sheets", "u1")).__name__ == "GoogleSheetsAssessmentAdapter"
    for factory in (get_metadata_adapter, get_adapter):
        with pytest.raises(ValueError, match="Unsupported connector type: file"):
            factory("file", "u1")
        with pytest.raises(ValueError, match="Unsupported connector type: gsheets"):
            factory("gsheets", "u1")


def _hubspot_spec(**overrides) -> ConnectorSpec:
    base = dict(
        connector_type="hubspot",
        display_name="HubSpot",
        aliases=("hs",),
        extract_tool="extract_hubspot",
        operations=frozenset({"extract", "map", "migrate", "sync"}),
        operation_tools={"sync": "hubspot_sync", "create": "should_not_override"},
        metadata_adapter=MagicMock(return_value="hubspot-meta"),
    )
    base.update(overrides)
    return ConnectorSpec(**base)


def test_registered_connector_is_used_everywhere(isolated_registry):
    spec = _hubspot_spec()
    register_connector(spec)

    assert canonicalize_connector("HS") == "hubspot"
    assert is_known_connector("hubspot")
    assert _normalize_connector_type("hs") == "hubspot"
    assert resolve_tool_id("extract", "hs") == "extract_hubspot"
    assert resolve_tool_id("sync") == "hubspot_sync"
    assert resolve_tool_id("create") == "google_sheets_create_from_prompt"
    assert supports_operation("hubspot", "sync") is True
    assert supports_operation("hubspot", "join") is False
    assert get_metadata_adapter("hubspot", "u1") == "hubspot-meta"
    spec.metadata_adapter.assert_called_once_with("u1")
    with pytest.raises(ValueError):
        get_adapter("hubspot", "u1")


def test_register_validation(isolated_registry):
    with pytest.raises(ValueError, match="already registered"):
        register_connector(ConnectorSpec(connector_type="sql", display_name="x"))
    register_connector(ConnectorSpec(connector_type="sql", display_name="SQL v2"), replace=True)
    assert get_connector("sql").display_name == "SQL v2"
    for bad in ("", "HubSpot", " hubspot"):
        with pytest.raises(ValueError, match="lowercase"):
            register_connector(ConnectorSpec(connector_type=bad, display_name="x"))


def test_entry_point_plugins_load_once(isolated_registry):
    good = MagicMock()
    good.load.return_value = lambda: register_connector(_hubspot_spec())
    broken = MagicMock()
    broken.name = "broken"
    broken.load.side_effect = ImportError("missing")

    reg._plugins_loaded = False
    with patch.object(reg, "entry_points", return_value=[broken, good]) as eps:
        assert "hubspot" in reg.connector_types()
        reg.connector_types()
    eps.assert_called_once_with(group=reg.ENTRY_POINT_GROUP)
