"""Unit tests for enterprise graph entity linker."""
from services.enterprise_graph.inventory import SourceEntity, normalize_entity_name
from services.enterprise_graph.linker import (
    build_candidate_pairs,
    build_cross_links_from_rows,
    candidates_to_mapping_clusters,
    score_pair,
)


def test_normalize_entity_name_plural():
    assert normalize_entity_name("Customers") == "customer"
    assert normalize_entity_name("Account") == "account"


def test_score_pair_account_customers():
    a = SourceEntity("m1", "salesforce", "Account", "Account", "account", domain_tags=["party"])
    b = SourceEntity("m2", "sql", "dbo.customers", "customers", "customer", domain_tags=["party"])
    score, _ = score_pair(a, b)
    assert score >= 0.55


def test_candidates_cluster_high_confidence():
    inventory = [
        SourceEntity("m1", "salesforce", "Account", "Account", "account"),
        SourceEntity("m2", "sql", "customers", "customers", "customer"),
    ]
    candidates = build_candidate_pairs(inventory, [])
    clusters = candidates_to_mapping_clusters(candidates, threshold=0.5)
    assert len(clusters) >= 1
    assert len(clusters[0]["entities"]) == 2


def test_cross_links_from_rows():
    rows = [
        {
            "canonical_entity_id": "c1",
            "source_member_id": "m1",
            "source_entity_id": "Account",
            "confidence": 0.9,
            "status": "suggested",
            "mapping_kind": "heuristic",
        },
        {
            "canonical_entity_id": "c1",
            "source_member_id": "m2",
            "source_entity_id": "customers",
            "confidence": 0.9,
            "status": "suggested",
            "mapping_kind": "heuristic",
        },
    ]
    from services.enterprise_graph.models import GraphMember

    members = [
        GraphMember(
            id="m1",
            graph_id="g1",
            connector_type="salesforce",
            connector_ref={},
            role="source",
            display_name="SF Prod",
            sort_order=0,
        ),
        GraphMember(
            id="m2",
            graph_id="g1",
            connector_type="sql",
            connector_ref={},
            role="source",
            display_name="Warehouse DB",
            sort_order=1,
        ),
    ]
    links = build_cross_links_from_rows(rows, members)
    assert len(links) == 1
    assert links[0]["source_entity_id"] == "Account"
    assert links[0]["target_entity_id"] == "customers"
