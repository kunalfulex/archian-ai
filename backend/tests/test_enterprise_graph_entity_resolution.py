"""Tests for enterprise graph entity resolution (priors + inventory alignment)."""
from services.enterprise_graph.entity_resolution import (
    entity_label_for,
    inventory_entity_keys,
    resolve_entity_ref,
    resolve_flow_priors,
)
from services.enterprise_graph.inventory import SourceEntity
from services.enterprise_graph.linker import build_candidate_pairs


def _gs_entity(member_id: str, eid: str, label: str, object_name: str = "") -> SourceEntity:
    return SourceEntity(
        member_id=member_id,
        connector_type="google_sheets",
        entity_id=eid,
        label=label,
        normalized_name="archian_data",
        object_name=object_name or label,
    )


def test_resolve_entity_ref_by_object_name():
    inventory = [
        _gs_entity("gs1", "sid123::Sheet1", "Archian Data / Sheet1", "Customer Contact Details"),
        SourceEntity("sql1", "sql", "users", "users", "user"),
    ]
    assert resolve_entity_ref("gs1", "Customer Contact Details", inventory) == "sid123::Sheet1"
    assert resolve_entity_ref("gs1", "Archian Data / Sheet1", inventory) == "sid123::Sheet1"
    assert resolve_entity_ref("gs1", "sid123::Sheet1", inventory) == "sid123::Sheet1"


def test_resolve_entity_ref_returns_none_for_orphan():
    inventory = [
        _gs_entity("gs1", "sid123::Sheet1", "Archian Data / Sheet1", "Archian Data"),
    ]
    assert resolve_entity_ref("gs1", "Customer Contact Details", inventory) is None


def test_resolve_flow_priors_filters_orphan_endpoints():
    inventory = [
        _gs_entity("gs1", "sid::Sheet1", "Archian Data / Sheet1"),
        SourceEntity("sql1", "sql", "users", "users", "user"),
    ]
    priors = [
        {
            "source_member_id": "sql1",
            "source_entity_id": "users",
            "target_member_id": "gs1",
            "target_entity_id": "Customer Contact Details",
            "confidence": 0.92,
            "mapping_kind": "flow_prior",
        }
    ]
    assert resolve_flow_priors(priors, inventory) == []


def test_resolve_flow_priors_rewrites_to_canonical_entity_ids():
    inventory = [
        _gs_entity("gs1", "sid::Sheet1", "Archian Data / Sheet1", "Customer Contact Details"),
        SourceEntity("sql1", "sql", "users", "users", "user"),
    ]
    priors = [
        {
            "source_member_id": "sql1",
            "source_entity_id": "users",
            "target_member_id": "gs1",
            "target_entity_id": "Customer Contact Details",
            "confidence": 0.92,
            "mapping_kind": "flow_prior",
        }
    ]
    resolved = resolve_flow_priors(priors, inventory)
    assert len(resolved) == 1
    assert resolved[0]["target_entity_id"] == "sid::Sheet1"


def test_build_candidate_pairs_does_not_inject_orphan_prior():
    inventory = [
        _gs_entity("gs1", "sid::Sheet1", "Archian Data / Sheet1"),
        SourceEntity("sql1", "sql", "users", "users", "user"),
    ]
    priors = [
        {
            "source_member_id": "sql1",
            "source_entity_id": "users",
            "target_member_id": "gs1",
            "target_entity_id": "Customer Contact Details",
            "confidence": 0.92,
            "mapping_kind": "flow_prior",
        }
    ]
    candidates = build_candidate_pairs(inventory, priors)
    orphan = [
        c
        for c in candidates
        if c.get("target_entity_id") == "Customer Contact Details"
        or c.get("source_entity_id") == "Customer Contact Details"
    ]
    assert orphan == []


def test_entity_label_for():
    inventory = [_gs_entity("gs1", "sid::Sheet1", "Archian Data / Sheet1")]
    assert entity_label_for("gs1", "sid::Sheet1", inventory) == "Archian Data / Sheet1"


def test_inventory_entity_keys():
    inventory = [
        SourceEntity("m1", "sql", "users", "users", "user"),
        _gs_entity("gs1", "sid::Sheet1", "Archian Data / Sheet1"),
    ]
    keys = inventory_entity_keys(inventory)
    assert ("m1", "users") in keys
    assert ("gs1", "sid::Sheet1") in keys
