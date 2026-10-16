"""Plan step args should stamp SQL join partners, not SF parent-only keys."""
from __future__ import annotations

from services.planning.plan_builder import _stamp_entity_params


def test_stamp_entity_params_sql_related_includes_child_table():
    classification = {
        "plan_contract": {
            "source": {"entity_key": "users", "entity_label": "Users"},
        },
        "migration_intent": {
            "include_related_data": True,
            "driving_entity_key": "user_plans",
            "parent_entity_key": "users",
            "extract_field_scope": "all_descriptive",
        },
    }
    params = _stamp_entity_params(classification, destination="unknown")
    assert params["source_entity_key"] == "users"
    assert params["related_entity_keys"] == ["user_plans"]
