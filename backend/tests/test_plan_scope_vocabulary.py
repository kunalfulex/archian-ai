"""PLAN_SCOPE prompt includes user vocabulary for map/migrate/data ops."""
from __future__ import annotations

from services.planning.goal_parser import (
    _PLAN_SCOPE_SINGLE_COMPOUND_RULES,
    _PLAN_SCOPE_USER_VOCABULARY,
)


def test_plan_scope_vocabulary_covers_map_migrate_data_ops():
    vocab = _PLAN_SCOPE_USER_VOCABULARY.lower()
    for phrase in (
        "map",
        "remap",
        "migrate",
        "move",
        "export to",
        "copy",
        "send",
        "load",
        "clean",
        "filter",
        "validate",
        "transform",
        "extracted data to",
    ):
        assert phrase in vocab, f"missing vocabulary hint: {phrase}"


def test_single_compound_rules_include_vocabulary():
    rules = _PLAN_SCOPE_SINGLE_COMPOUND_RULES.lower()
    assert "user vocabulary" in rules
    assert "move" in rules
    assert "export/pull/fetch" in rules or "export to" in rules
