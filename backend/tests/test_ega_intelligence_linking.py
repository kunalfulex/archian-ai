"""Tests for EGA LLM-first linking bundle and validators."""
from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.models import GraphMember
from services.semantic_mapping.ega_intelligence_bundle import (
    member_option_key,
    parse_cross_map_member_pairs,
)
from services.semantic_mapping.linking_validator import validate_entity_mapping_clusters


def _member(mid: str, ct: str, cid: str, role: str = "source") -> GraphMember:
    ref = (
        {"google_sheets_connection_id": cid, "connection_id": cid}
        if ct == "google_sheets"
        else {"connector_id": cid}
    )
    return GraphMember(
        id=mid,
        graph_id="g1",
        connector_type=ct,
        connector_ref=ref,
        role=role,
        display_name=ct,
        sort_order=0,
    )


def test_member_option_key_google_sheets():
    m = _member("m1", "google_sheets", "conn-abc")
    assert member_option_key(m) == "google_sheets:conn-abc"


def test_parse_cross_map_member_pairs():
    sql = _member("sql1", "sql", "db1")
    gs = _member("gs1", "google_sheets", "gs1")
    settings = {
        "layout": {
            "nodes": [
                {"id": "n1", "optionKey": "sql:db1"},
                {"id": "n2", "optionKey": "google_sheets:gs1"},
            ],
            "edges": [
                {
                    "fromNodeId": "n2",
                    "toNodeId": "n1",
                    "linkType": "cross_map",
                }
            ],
        }
    }
    pairs = parse_cross_map_member_pairs(settings, [sql, gs])
    assert pairs == [("gs1", "sql1")]


def test_validate_entity_mapping_clusters_accepts_cross_member_cluster():
    inventory = [
        SourceEntity("sql1", "sql", "users", "users", "user"),
        SourceEntity(
            "gs1",
            "google_sheets",
            "sid::Sheet1",
            "Salesforce Contacts / Sheet1",
            "contact",
            category="Contact",
            object_name="Contact",
        ),
    ]
    raw = {
        "clusters": [
            {
                "canonical_name": "Contact",
                "confidence": 0.8,
                "evidence": "Same contact semantics",
                "entities": [
                    {"member_id": "sql1", "entity_id": "users"},
                    {"member_id": "gs1", "entity_id": "sid::Sheet1"},
                ],
            }
        ]
    }
    clusters, warnings = validate_entity_mapping_clusters(raw, inventory, min_confidence=0.55)
    assert not warnings or all("below" not in w for w in warnings)
    assert len(clusters) == 1
    assert len(clusters[0]["entities"]) == 2


def test_validate_entity_mapping_clusters_rejects_single_member():
    inventory = [
        SourceEntity("sql1", "sql", "users", "users", "user"),
        SourceEntity("sql1", "sql", "orders", "orders", "order"),
    ]
    raw = {
        "clusters": [
            {
                "canonical_name": "Bad",
                "confidence": 0.9,
                "entities": [
                    {"member_id": "sql1", "entity_id": "users"},
                    {"member_id": "sql1", "entity_id": "orders"},
                ],
            }
        ]
    }
    clusters, _ = validate_entity_mapping_clusters(raw, inventory)
    assert clusters == []


def test_validate_entity_mapping_clusters_rejects_multiple_per_member():
    inventory = [
        SourceEntity("sql1", "sql", "users", "users", "user"),
        SourceEntity("sql1", "sql", "orders", "orders", "order"),
        SourceEntity(
            "gs1",
            "google_sheets",
            "sid::Sheet1",
            "Contacts / Sheet1",
            "contact",
        ),
    ]
    raw = {
        "clusters": [
            {
                "canonical_name": "Mega",
                "confidence": 0.82,
                "entities": [
                    {"member_id": "sql1", "entity_id": "users"},
                    {"member_id": "sql1", "entity_id": "orders"},
                    {"member_id": "gs1", "entity_id": "sid::Sheet1"},
                ],
            }
        ]
    }
    clusters, warnings = validate_entity_mapping_clusters(raw, inventory)
    assert clusters == []
    assert any("one entity per connector" in w for w in warnings)
