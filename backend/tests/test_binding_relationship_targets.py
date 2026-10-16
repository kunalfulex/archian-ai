"""Tests for relationship target enrichment in plan binding."""
from __future__ import annotations

from services.planning.binding.contract import EntityCandidate, RelationshipTargetCandidate
from services.planning.binding.relationship_targets import build_relationship_targets
from services.planning.binding.retrieve import candidates_to_prompt_json


CONTACT_SEMANTICS = {
    "primary": {
        "category": "Customer Contact Information",
        "description": "Individual contacts with name, email, and phone.",
    },
    "synonyms": ["client contact", "customer details"],
    "relationship_hints": [
        {"field": "AccountId", "relationship": "lookup", "related_entity": "Account"},
        {"field": "OwnerId", "relationship": "lookup", "related_entity": "User"},
        {"field": "Case_Lookup__c", "relationship": "lookup", "related_entity": "Case"},
    ],
}

ACCOUNT_SEMANTICS = {
    "primary": {
        "category": "Business Account",
        "description": "Company or business the contact belongs to.",
    },
    "synonyms": ["business account", "company"],
}


def test_build_relationship_targets_includes_account_semantics():
    catalog = {
        "Contact": {"entity_key": "Contact", "label": "Contact"},
        "Account": {"entity_key": "Account", "label": "Account"},
    }
    targets = build_relationship_targets(
        primary_semantics=CONTACT_SEMANTICS,
        semantics_map={"Contact": CONTACT_SEMANTICS, "Account": ACCOUNT_SEMANTICS},
        catalog_by_key=catalog,
        prompt="customer contact details with their business details from SF 2",
    )
    keys = [t.related_entity_key for t in targets]
    assert "Account" in keys
    account = next(t for t in targets if t.related_entity_key == "Account")
    assert account.category == "Business Account"
    assert account.description
    assert "business account" in account.synonyms
    assert account.lookup_field == "AccountId"


def test_build_relationship_targets_deprioritizes_user_without_prompt():
    catalog = {
        "Contact": {"entity_key": "Contact", "label": "Contact"},
        "User": {"entity_key": "User", "label": "User"},
    }
    targets = build_relationship_targets(
        primary_semantics=CONTACT_SEMANTICS,
        semantics_map={"Contact": CONTACT_SEMANTICS, "User": {"primary": {"category": "User"}}},
        catalog_by_key=catalog,
        prompt="customer contact with business details",
    )
    keys = [t.related_entity_key for t in targets]
    assert "User" not in keys


def test_candidates_to_prompt_json_includes_relationship_targets():
    candidate = EntityCandidate(
        entity_key="Contact",
        label="Contact",
        category="Customer Contact Information",
        description="Individual contacts",
        relationship_targets=[
            RelationshipTargetCandidate(
                related_entity_key="Account",
                lookup_field="AccountId",
                label="Account",
                category="Business Account",
                description="Company details",
                synonyms=["business account"],
                suggested_match_score=120.0,
            )
        ],
        fields=[],
    )
    raw = candidates_to_prompt_json([candidate])
    assert "relationship_targets" in raw
    assert "Account" in raw
    assert "Business Account" in raw
    assert "business account" in raw
