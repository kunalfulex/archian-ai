"""Continuation scope cap — migrate→map dependency only."""
from __future__ import annotations

from services.planning.plan_scope import apply_continuation_scope_cap


def test_data_ops_phase_preserves_map_only_from_plan_scope():
    goal = {
        "operations": ["map"],
        "data_operations": ["validate", "filter"],
        "needs_destination": True,
        "destination_phrase": "Test DB",
    }
    capped = apply_continuation_scope_cap(goal, continuation_phase="data_ops", mapping_ready=False)
    assert capped.get("operations") == ["map"]
    assert capped.get("needs_destination") is True
    assert capped.get("destination_phrase") == "Test DB"


def test_data_ops_phase_preserves_map_migrate_compound():
    goal = {
        "operations": ["map", "migrate"],
        "data_operations": ["filter"],
        "needs_destination": True,
        "destination_phrase": "SF 1",
    }
    capped = apply_continuation_scope_cap(goal, continuation_phase="data_ops", mapping_ready=False)
    assert capped["operations"] == ["map", "migrate"]


def test_migrate_in_scope_adds_map_when_not_ready():
    goal = {"operations": ["migrate"], "needs_destination": True}
    capped = apply_continuation_scope_cap(goal, continuation_phase="migrate", mapping_ready=False)
    assert capped["operations"] == ["map", "migrate"]


def test_migrate_only_when_mapping_ready():
    goal = {"operations": ["migrate"], "needs_destination": True}
    capped = apply_continuation_scope_cap(goal, continuation_phase="migrate", mapping_ready=True)
    assert capped["operations"] == ["migrate"]
