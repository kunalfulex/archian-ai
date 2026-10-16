"""Tests for SOQL extract contract (authority-driven SOQL generation)."""
from __future__ import annotations

from services.planning.soql_extract_contract import (
    build_soql_extract_contract,
    enforce_soql_select_contract,
    soql_authority_prompt_block,
    validate_soql_referenced_fields,
)


def _opportunity_object_result():
    return {
        "object_api_name": "Opportunity",
        "object_label": "Opportunity",
        "selected_fields": [
            "Id",
            "CloseDate",
            "StageName",
            "Amount",
            "Account.Name",
            "Account.Phone",
        ],
        "filters": None,
        "field_metadata": [
            {"api_name": "Id", "label": "Record ID"},
            {"api_name": "CloseDate", "label": "Close Date"},
            {"api_name": "StageName", "label": "Stage"},
            {"api_name": "Amount", "label": "Amount"},
            {
                "api_name": "Account.Name",
                "label": "Account Name",
                "is_relationship_field": True,
                "relationship_name": "Account",
                "lookup_object": "Account",
            },
            {
                "api_name": "Account.Phone",
                "label": "Account Phone",
                "is_relationship_field": True,
                "relationship_name": "Account",
                "lookup_object": "Account",
            },
        ],
        "lookup_relationships": {
            "Account": [{"field_api_name": "AccountId", "relationship_name": "Account"}],
        },
        "extract_field_scope": {
            "include_related_data": True,
            "parent_entity_keys": ["Account"],
        },
    }


def test_build_contract_pins_account_only():
    contract = build_soql_extract_contract(_opportunity_object_result())
    assert contract["primary_object"] == "Opportunity"
    assert "Account.Name" in contract["allowed_select_fields"]
    assert "Account" in contract["allowed_relationship_prefixes"]
    assert "Account" in contract["pinned_related_entity_keys"]


def test_authority_prompt_demotes_user_text():
    contract = build_soql_extract_contract(_opportunity_object_result())
    block = soql_authority_prompt_block(contract)
    assert "AUTHORITATIVE" in block
    assert "NON-AUTHORITATIVE" in block
    assert "Account.Name" in block
    assert "Do NOT add SELECT fields" in block or "Do NOT add fields" in block


def test_enforce_strips_unauthorized_contact_field():
    soql = (
        "SELECT CloseDate, StageName, Amount, Account.Name, Contact.LastName, Id "
        "FROM Opportunity"
    )
    allowed = [
        "Id",
        "CloseDate",
        "StageName",
        "Amount",
        "Account.Name",
        "Account.Phone",
    ]
    updated, kept, removed = enforce_soql_select_contract(soql, allowed)
    assert "Contact.LastName" in removed
    assert "Contact.LastName" not in updated
    assert "Account.Name" in updated
    assert "Account.Name" in kept


def test_validate_rejects_contact_relationship_on_opportunity():
    obj = _opportunity_object_result()
    valid, invalid, _ = validate_soql_referenced_fields(
        ["CloseDate", "Account.Name", "Contact.LastName"],
        obj,
    )
    assert valid is False
    assert "Contact.LastName" in invalid


def test_validate_accepts_authorized_relationship_paths():
    obj = _opportunity_object_result()
    valid, invalid, _ = validate_soql_referenced_fields(
        ["CloseDate", "Account.Name", "StageName"],
        obj,
    )
    assert valid is True
    assert invalid == []


def test_contract_merges_migration_intent():
    obj = _opportunity_object_result()
    obj["extract_field_scope"] = {}
    contract = build_soql_extract_contract(
        obj,
        migration_intent={
            "include_related_data": True,
            "parent_entity_key": "Account",
            "field_hints_strict": True,
            "field_hints": ["amount", "stage"],
        },
    )
    assert contract["include_related_data"] is True
    assert "Account" in contract["pinned_related_entity_keys"]
    block = soql_authority_prompt_block(contract)
    assert "STRICT FIELD MODE" in block
