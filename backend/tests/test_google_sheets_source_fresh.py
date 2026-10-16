"""Google Sheets source pin + clarification tests (mirrors destination)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.connector_guards import (
    classification_source_endpoint_resolved,
    validate_classification_resources,
)
from services.planning.gap_detector import (
    detect_clarifications,
    resolve_source_connector_clarification,
)
from services.planning.google_sheets_source import (
    apply_google_sheets_source_account_pick,
    gsheets_workbook_source_resolved,
    normalize_google_sheets_source_pins,
)
from services.planning.connector_llm_resolver import LlmConnectorPick
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)


def _catalog() -> ResourceCatalog:
    return ResourceCatalog(
        has_google_sheets=True,
        google_sheets_connections=[
            GoogleSheetsConnectionRef(connection_id="conn-xtag", name="xTag account"),
        ],
        google_sheet_rows=[
            GoogleSheetRef(
                spreadsheet_name="Employee list",
                sheet_name="Sheet1",
                spreadsheet_id="sheet-emp-1",
                google_sheets_connection_id="conn-xtag",
            ),
        ],
    )


def test_gs_account_only_not_source_endpoint_resolved():
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_connector_id": "conn-xtag",
        "intent": "extract",
        "operations": ["extract"],
    }
    assert not classification_source_endpoint_resolved(cls, catalog=cat)
    assert not gsheets_workbook_source_resolved(cls, cat)


def test_gs_workbook_pins_source_endpoint_resolved():
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_connector_id": "conn-xtag",
        "google_sheets_connection_id": "conn-xtag",
        "source_name": "Employee list",
        "source_spreadsheet_id": "sheet-emp-1",
        "_source_endpoint_clarified": True,
        "_source_connector_validated": True,
        "intent": "extract",
        "operations": ["extract"],
    }
    assert gsheets_workbook_source_resolved(cls, cat)
    assert classification_source_endpoint_resolved(cls, catalog=cat)


def test_normalize_clears_account_name_from_source_name():
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_name": "xTag account",
        "source_connector_id": "conn-xtag",
    }
    out = normalize_google_sheets_source_pins(cls, cat)
    assert out.get("source_name") is None
    assert out.get("source_account_name") == "xTag account"


def test_account_pick_does_not_set_workbook_source_name():
    cat = _catalog()
    out = apply_google_sheets_source_account_pick(
        {
            "source": "google_sheets",
            "intent": "extract",
            "operations": ["extract"],
            "_plan_scope": {"operations": ["extract"], "needs_source": True},
        },
        LlmConnectorPick("google_sheets", "xTag account", "conn-xtag", 0.9),
        cat,
    )
    assert out.get("source_name") is None
    assert out["source_account_name"] == "xTag account"
    assert out["google_sheets_connection_id"] == "conn-xtag"
    assert not out.get("_source_endpoint_clarified")
    assert not classification_source_endpoint_resolved(out, catalog=cat)


def test_account_only_not_endpoint_resolved_without_catalog():
    """GS account pin must not bypass workbook validation when catalog omitted."""
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_connector_id": "conn-xtag",
        "_source_endpoint_clarified": True,
        "intent": "extract",
        "operations": ["extract"],
    }
    assert not classification_source_endpoint_resolved(cls, catalog=cat)
    assert not classification_source_endpoint_resolved(cls)


def test_resolve_source_connector_clarification_account_only_when_type_known():
    """Workbook picker is entity-layer; connector clar returns account or None."""
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_connector_id": "conn-xtag",
        "google_sheets_connection_id": "conn-xtag",
        "source_account_name": "xTag account",
        "_source_connector_validated": True,
        "intent": "extract",
        "operations": ["extract"],
        "_plan_scope": {"operations": ["extract"], "needs_source": True},
    }
    clar = resolve_source_connector_clarification(cls, cat, "extract employee list")
    assert clar is None


def test_detect_clarifications_extract_gs_source_workbook():
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_connector_id": "conn-xtag",
        "google_sheets_connection_id": "conn-xtag",
        "intent": "extract",
        "operations": ["extract"],
        "_plan_scope": {"operations": ["extract"], "needs_source": True, "needs_destination": False},
    }

    async def _run():
        return await detect_clarifications(
            cls,
            "extract employee list from google sheet from xTag account",
            cat,
        )

    gaps = asyncio.run(_run())
    types = [g.clarification_type for g in gaps]
    assert "pick_source_workbook" in types
    assert "pick_source_entity" not in types


def test_validate_preserves_workbook_clears_connection_as_source_name():
    cat = _catalog()
    cls = {
        "source": "google_sheets",
        "source_name": "xTag account",
        "source_connector_id": "conn-xtag",
    }
    out = validate_classification_resources(cls, cat)
    assert out.get("source_name") is None
    assert out.get("source_account_name") == "xTag account"
