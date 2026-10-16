"""Tests for map-step migration intent context."""
from __future__ import annotations

from services.planning.plan_builder import (
    audit_migration_intent_scope,
    build_mapping_relationship_context_block,
    enrich_mapping_instruction,
    merge_intent_issues_into_audit,
    migration_intent_from_snapshot,
)


def test_migration_intent_from_plan_contract():
    snap = {
        "plan_contract": {
            "migration_intent": {
                "cross_system": True,
                "driving_entity_key": "Contact",
                "destination_entity_key": "Contact",
                "parent_entity_key": "Account",
                "include_related_data": True,
            }
        }
    }
    intent = migration_intent_from_snapshot(snap)
    assert intent["driving_entity_key"] == "Contact"


def test_mapping_block_uses_destination_entity_key():
    block = build_mapping_relationship_context_block(
        {
            "cross_system": True,
            "destination_entity_key": "Account",
            "driving_entity_key": "Contact",
        }
    )
    assert "Account" in block
    assert "Primary destination object" in block


def test_mapping_block_mentions_dest_and_parent():
    block = build_mapping_relationship_context_block(
        {
            "cross_system": True,
            "destination_entity_key": "Contact",
            "parent_entity_key": "Account",
            "include_related_data": True,
        }
    )
    assert "Contact" in block
    assert "Account" in block
    assert "lookup_of" in block


def test_enrich_mapping_instruction_prepends_block():
    out = enrich_mapping_instruction(
        "map email and phone",
        {
            "plan_contract": {
                "migration_intent": {
                    "cross_system": True,
                    "destination_entity_key": "Contact",
                }
            }
        },
    )
    assert "Contact" in (out or "")
    assert "map email and phone" in (out or "")


def test_audit_warns_dest_target_mismatch():
    issues = audit_migration_intent_scope(
        {
            "FirstName": {"object": "Account", "field": "Name"},
        },
        {"FirstName": "Ann"},
        {
            "cross_system": True,
            "destination_entity_key": "Contact",
        },
    )
    codes = [i["code"] for i in issues]
    assert "DEST_TARGET_MISMATCH" in codes


def test_merge_intent_issues_sets_warn_status():
    merged = merge_intent_issues_into_audit(
        {"status": "ok", "issues": []},
        [
            {
                "code": "DEST_TARGET_MISMATCH",
                "severity": "warn",
                "message": "test",
                "source_keys": ["FirstName"],
            }
        ],
    )
    assert merged["status"] == "warn"
