"""Tests for authoritative extract contracts (Phase 4)."""
from __future__ import annotations

from services.planning.extract_contract_from_classification import (
    build_extract_contract_from_classification,
)
from services.planning.extract_contract_gates import extract_contract_is_authoritative


def _bound_classification(**extra):
    base = {
        "source": "salesforce",
        "source_name": "Prod",
        "plan_contract": {
            "source": {
                "entity_key": "Contact",
                "entity_label": "Contact",
                "resolution_source": "llm_bind",
                "confidence": 0.92,
                "connector_type": "salesforce",
            },
            "migration_intent": {
                "driving_entity_key": "Contact",
                "field_hints": ["Email", "LastName"],
                "field_hints_strict": True,
                "extract_field_scope": "explicit",
                "extract_fields_confirmed": True,
            },
        },
        "migration_intent": {
            "driving_entity_key": "Contact",
            "field_hints": ["Email", "LastName"],
            "field_hints_strict": True,
            "extract_field_scope": "explicit",
            "extract_fields_confirmed": True,
        },
    }
    base.update(extra)
    return base


def test_sf_extract_contract_binding_authoritative():
    contract = build_extract_contract_from_classification(_bound_classification(), "salesforce")
    assert contract.get("binding_authoritative") is True
    assert contract.get("primary_object") == "Contact"
    assert extract_contract_is_authoritative(contract) is True


def test_sf_extract_contract_not_authoritative_without_binding():
    contract = build_extract_contract_from_classification(
        {
            "source_object_api_name": "Contact",
            "migration_intent": {"field_hints": ["Email"]},
        },
        "salesforce",
    )
    assert not contract.get("binding_authoritative")
    assert extract_contract_is_authoritative(contract) is False


def test_sql_extract_contract_binding_authoritative():
    contract = build_extract_contract_from_classification(
        _bound_classification(
            source="sql",
            plan_contract={
                "source": {
                    "entity_key": "users",
                    "resolution_source": "llm_bind",
                    "confidence": 0.9,
                    "connector_type": "sql",
                },
                "migration_intent": {
                    "field_hints": ["email"],
                    "extract_fields_confirmed": True,
                    "extract_field_scope": "explicit",
                },
            },
            migration_intent={
                "field_hints": ["email"],
                "extract_fields_confirmed": True,
                "extract_field_scope": "explicit",
            },
        ),
        "sql",
    )
    assert contract.get("binding_authoritative") is True
    assert contract.get("primary_table") == "users"
    assert extract_contract_is_authoritative(contract) is True
