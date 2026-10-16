"""Tests for enterprise graph canonical model builder."""
from services.enterprise_graph.canonical_builder import (
    build_canonical_entities_from_mappings,
    build_canonical_graph,
    build_entity_to_canonical_map,
)
from services.enterprise_graph.inventory import SourceEntity


def test_build_canonical_entities_from_cluster():
    cid = "canon-1"
    inventory = [
        SourceEntity("m1", "salesforce", "Account", "Account", "account", domain_tags=["party"]),
        SourceEntity("m2", "sql", "customers", "customers", "customer", domain_tags=["party"]),
    ]
    mappings = [
        {
            "canonical_entity_id": cid,
            "source_member_id": "m1",
            "source_entity_id": "Account",
            "confidence": 0.9,
            "status": "confirmed",
        },
        {
            "canonical_entity_id": cid,
            "source_member_id": "m2",
            "source_entity_id": "customers",
            "confidence": 0.9,
            "status": "confirmed",
        },
    ]
    entities = build_canonical_entities_from_mappings(
        graph_id="g1",
        mappings=mappings,
        inventory=inventory,
        min_confidence=0.5,
    )
    assert len(entities) == 1
    assert entities[0]["id"] == cid
    assert entities[0]["status"] == "approved"
    assert len(entities[0]["source_lineage"]) == 2


def test_canonical_graph_projects_fk_edge():
    cid_customer = "c-customer"
    cid_order = "c-order"
    inventory = [
        SourceEntity("m1", "salesforce", "Account", "Account", "account"),
        SourceEntity("m1", "salesforce", "Order", "Order", "order"),
    ]
    mappings = [
        {
            "canonical_entity_id": cid_customer,
            "source_member_id": "m1",
            "source_entity_id": "Account",
            "confidence": 0.95,
            "status": "confirmed",
        },
    ]
    entities = build_canonical_entities_from_mappings(
        graph_id="g1",
        mappings=mappings,
        inventory=inventory,
        min_confidence=0.5,
    )
    entities.append(
        {
            "id": cid_order,
            "graph_id": "g1",
            "name": "Order",
            "domain_tags": ["commerce"],
            "attributes": {},
            "source_lineage": [{"member_id": "m1", "entity_id": "Order", "label": "Order"}],
            "version": 1,
            "status": "draft",
        }
    )
    member_graphs = {
        "m1": {
            "nodes": [{"id": "Account"}, {"id": "Order"}],
            "edges": [
                {
                    "source_id": "Order",
                    "target_id": "Account",
                    "label": "AccountId",
                    "edge_type": "lookup",
                }
            ],
        }
    }
    graph = build_canonical_graph(
        canonical_entities=entities,
        mappings=mappings,
        inventory=inventory,
        member_graphs=member_graphs,
    )
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["source_id"] == cid_order
    assert graph["edges"][0]["target_id"] == cid_customer


def test_entity_to_canonical_map_excludes_rejected():
    mappings = [
        {
            "canonical_entity_id": "c1",
            "source_member_id": "m1",
            "source_entity_id": "A",
            "confidence": 0.9,
            "status": "rejected",
        },
    ]
    m = build_entity_to_canonical_map(mappings, min_confidence=0.5)
    assert m == {}
