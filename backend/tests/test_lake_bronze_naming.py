"""Tests for deterministic lake bronze table naming."""
from __future__ import annotations

from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.lake_designer import _entity_slug, bronze_table_name
from services.enterprise_graph.models import GraphMember


def test_gsheets_slug_uses_workbook_and_sheet_not_spreadsheet_id():
    ent = SourceEntity(
        member_id="m1",
        connector_type="google_sheets",
        entity_id="1ky6aowc1knordcwooaTpwcuc6ycp7atg7yoituasaq::Sheet1",
        label="Archian Data / Salesforce Contacts",
        normalized_name="archian_data_salesforce_contact",
        category="Contact",
    )
    slug = _entity_slug(ent)
    assert "1ky6aowc" not in slug
    assert "archian" in slug
    assert "salesforce" in slug or "contact" in slug


def test_bronze_table_name_format():
    member = GraphMember(
        id="m1",
        graph_id="g1",
        connector_type="google_sheets",
        connector_ref={"google_sheets_connection_id": "c1"},
        role="source",
        display_name="Archian AI",
        sort_order=0,
    )
    ent = SourceEntity(
        member_id="m1",
        connector_type="google_sheets",
        entity_id="spreadsheetId::Sheet1",
        label="Archian Data / Salesforce Contacts",
        normalized_name="contact",
    )
    name = bronze_table_name(ent, member)
    assert name.startswith("raw__archian_ai__")
    assert "spreadsheetid" not in name
