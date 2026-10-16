"""Tests for connector info context (profiles + schema graphs + semantics)."""
from __future__ import annotations

from services.planning.connector_info_context import (
    _format_entity_inventory,
    _format_profile_section,
    _format_relationship_summary,
    pick_relevant_entity_keys,
)
from services.planning.resource_catalog import ConnectorProfileSummary


def test_format_profile_section_with_synopsis():
    profile = ConnectorProfileSummary(
        synopsis="UK field service CRM",
        business_domains=["service", "operations"],
        synonyms=["SF 1", "service org"],
        entity_labeled_count=12,
        entity_total_count=40,
        profile_status="ready",
    )
    lines = _format_profile_section(
        display_name="SF 1",
        connector_type="salesforce",
        profile=profile,
    )
    text = "".join(lines)
    assert "SF 1" in text
    assert "field service CRM" in text
    assert "service org" in text
    assert "12/40" in text


def test_format_entity_inventory_with_semantics():
    semantics = {
        "Contact": {
            "primary": {"category": "Customer contacts", "description": "People we sell to"},
            "domains": ["sales"],
        }
    }
    graph = {
        "nodes": [{"id": "Contact", "label": "Contact", "node_type": "object"}],
        "edges": [],
    }
    lines = _format_entity_inventory(semantics, graph, focus_keys=["Contact"])
    text = "".join(lines)
    assert "Contact" in text
    assert "Customer contacts" in text


def test_format_relationship_summary():
    graph = {
        "nodes": [
            {"id": "Contact", "label": "Contact"},
            {"id": "Account", "label": "Account"},
        ],
        "edges": [
            {
                "source_id": "Contact",
                "target_id": "Account",
                "label": "AccountId",
                "edge_type": "lookup",
            }
        ],
    }
    lines = _format_relationship_summary(graph, focus_keys=["Contact"])
    text = "".join(lines)
    assert "Contact" in text
    assert "Account" in text
    assert "AccountId" in text


def test_pick_relevant_entity_keys_from_prompt():
    semantics = {
        "Contact": {
            "primary": {"category": "Customer contacts"},
            "synonyms": ["customer person"],
        }
    }
    graph = {"nodes": [{"id": "Contact", "label": "Contact"}], "edges": []}
    keys = pick_relevant_entity_keys(
        "What fields does Contact have?",
        semantics,
        graph,
    )
    assert keys == ["Contact"]
