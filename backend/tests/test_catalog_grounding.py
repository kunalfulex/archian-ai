"""Tests for unified catalog grounding (SQL, GSheets, Salesforce)."""
from __future__ import annotations

from services.planning.catalog_grounding import (
    resolve_phrase_to_catalog_key,
    scoped_side_text,
)


def test_scoped_side_text_prefers_goal_phrase_when_cross_system():
    goal = {"source_phrase": "user table", "destination_phrase": "contact object"}
    assert (
        scoped_side_text(goal, side="source", phrase="user", cross_system=True)
        == "user table"
    )
    assert (
        scoped_side_text(goal, side="destination", phrase="contact", cross_system=True)
        == "contact object"
    )


def test_sql_phrase_user_beats_salesforce_metadata_table_on_scoped_text():
    catalog = [
        {"entity_key": "user", "api_name": "user", "label": "user"},
        {
            "entity_key": "salesforce_object_record_types",
            "api_name": "salesforce_object_record_types",
            "label": "salesforce_object_record_types",
        },
    ]
    hit = resolve_phrase_to_catalog_key(
        "user table",
        catalog,
        "sql",
        scoped_text="user table",
        hints=["user table"],
        cross_system=True,
        allow_prompt_fallback=False,
    )
    assert hit is not None
    assert hit[0] == "user"


def test_scoped_side_text_empty_goal_returns_phrase():
    assert scoped_side_text(None, side="source", phrase="contacts", cross_system=False) == "contacts"
