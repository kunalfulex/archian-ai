"""Ensure canonical entities exist before semantic mapping inserts."""
from services.enterprise_graph.canonical_builder import build_canonical_entities_from_mappings
from services.enterprise_graph.inventory import SourceEntity


def test_build_canonical_stubs_from_suggested_rows():
    cid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    inventory = [
        SourceEntity("m1", "salesforce", "Account", "Account", "account"),
        SourceEntity("m2", "sql", "customers", "customers", "customer"),
    ]
    mappings = [
        {
            "canonical_entity_id": cid,
            "source_member_id": "m1",
            "source_entity_id": "Account",
            "confidence": 0.9,
            "status": "suggested",
        },
        {
            "canonical_entity_id": cid,
            "source_member_id": "m2",
            "source_entity_id": "customers",
            "confidence": 0.9,
            "status": "suggested",
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
