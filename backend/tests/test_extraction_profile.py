"""Tests for source-true extraction profile and rule sanitization."""
from utils.extraction_profile import (
    ExtractionProfile,
    resolve_extraction_profile,
    sanitize_extraction_rules_for_profile,
)


def test_resolve_goal_ops_source_true_migrate():
    profile = resolve_extraction_profile(
        goal={"operations": ["extract", "map", "migrate"]},
        source="sql",
    )
    assert profile == ExtractionProfile.SOURCE_TRUE_MIGRATE


def test_resolve_migrate_prompt_defaults_source_true():
    profile = resolve_extraction_profile(
        prompt="extract customer data from Test DB users and migrate to contact sheet",
        template="extract_map_migrate",
        source="sql",
    )
    assert profile == ExtractionProfile.SOURCE_TRUE_MIGRATE


def test_resolve_cleanup_is_explicit_ops():
    profile = resolve_extraction_profile(intent="cleanup", source="sql")
    assert profile == ExtractionProfile.EXPLICIT_OPS


def test_resolve_saved_flow_when_rules_present():
    profile = resolve_extraction_profile(
        saved_extraction_rules={
            "transformation_rules": {
                "fields": [{"source_field": "a", "action": "keep", "output_field": "a"}]
            }
        },
        prompt="migrate contacts",
        template="extract_map_migrate",
    )
    assert profile == ExtractionProfile.SAVED_FLOW


def test_sanitize_strips_destination_renames():
    rules = {
        "filter_rules": {"conditions": [{"field": "email", "operator": "contains", "value": "@"}], "logic": "AND"},
        "transformation_rules": {
            "fields": [
                {"source_field": "first_name", "action": "keep", "output_field": "Name"},
                {"source_field": "last_name", "action": "keep", "output_field": "Name"},
                {"source_field": "email", "action": "keep", "output_field": "email"},
            ]
        },
    }
    out = sanitize_extraction_rules_for_profile(
        rules,
        ExtractionProfile.SOURCE_TRUE_MIGRATE,
        source_schema=["first_name", "last_name", "email", "phone"],
    )
    assert "filter_rules" not in out
    fields = out.get("transformation_rules", {}).get("fields", [])
    assert len(fields) == 1
    assert fields[0]["source_field"] == "email"


def test_sanitize_allows_exclude():
    rules = {
        "transformation_rules": {
            "fields": [
                {"source_field": "internal_id", "action": "exclude"},
                {"source_field": "email", "action": "keep", "output_field": "email"},
            ]
        }
    }
    out = sanitize_extraction_rules_for_profile(
        rules,
        ExtractionProfile.SOURCE_TRUE_MIGRATE,
        source_schema=["internal_id", "email"],
    )
    fields = out["transformation_rules"]["fields"]
    assert len(fields) == 2
    actions = {(f["source_field"], f["action"]) for f in fields}
    assert ("internal_id", "exclude") in actions
    assert ("email", "keep") in actions
