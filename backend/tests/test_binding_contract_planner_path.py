"""Tests for binding-contract planner path guards."""
from __future__ import annotations

from services.planning.planner_path import classification_has_binding_contract


def _bound_classification():
    return {
        "_plan_scope": {"operations": ["extract", "map"]},
        "plan_contract": {
            "source": {
                "entity_key": "Contact",
                "resolution_source": "llm_bind",
                "confidence": 0.9,
            },
            "destination": {
                "entity_key": "Account",
                "resolution_source": "llm_bind",
                "confidence": 0.9,
            },
        },
    }


def test_binding_contract_detected():
    cls = _bound_classification()
    assert classification_has_binding_contract(cls) is True
