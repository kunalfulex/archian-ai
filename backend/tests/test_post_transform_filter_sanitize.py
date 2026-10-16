"""Filter rules must target columns that survive transform (merge-before-filter)."""

from utils.extraction_rule_pipeline import apply_extraction_rules
from utils.extraction_rules_validate import (
    align_filter_rules_with_transform,
    fields_removed_by_transform,
    predict_post_transform_schema,
    sanitize_filter_rules_for_post_transform_schema,
)


def test_predict_post_transform_schema_after_merge():
    schema = ["Email", "FirstName", "LastName"]
    tr = {
        "fields": [
            {
                "action": "merge",
                "source_fields": ["FirstName", "LastName"],
                "output_field": "Name",
            }
        ]
    }
    post = predict_post_transform_schema(schema, tr)
    assert set(post) == {"Email", "Name"}
    assert fields_removed_by_transform(tr) == ["FirstName", "LastName"]


def test_sanitize_drops_filter_on_merged_away_columns():
    post = ["Email", "Name"]
    removed = ["FirstName", "LastName"]
    fr = {
        "conditions": [
            {"field": "Email", "operator": "valid_email"},
            {"field": "FirstName", "operator": "is_not_empty"},
            {"field": "LastName", "operator": "is_not_empty"},
        ],
        "logic": "AND",
    }
    out = sanitize_filter_rules_for_post_transform_schema(
        fr, post, removed_field_hints=removed
    )
    conds = out["conditions"]
    assert len(conds) == 1
    assert conds[0]["field"] == "Email"
    assert conds[0]["operator"] == "valid_email"


def test_sanitize_normalizes_email_field_casing():
    post = ["Email", "Name"]
    fr = {
        "conditions": [{"field": "email", "operator": "valid_email"}],
        "logic": "AND",
    }
    out = sanitize_filter_rules_for_post_transform_schema(fr, post)
    assert out["conditions"][0]["field"] == "Email"


def test_apply_merge_then_filter_invalid_email_only():
    """Regression: combine + invalid email must not zero rows via spurious is_not_empty."""
    data = [
        {"Email": "bad", "FirstName": "A", "LastName": "B"},
        {"Email": "ok@example.com", "FirstName": "C", "LastName": "D"},
    ]
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "action": "merge",
                    "source_fields": ["FirstName", "LastName"],
                    "output_field": "Name",
                }
            ]
        },
        "filter_rules": {
            "conditions": [
                {"field": "Email", "operator": "valid_email"},
                {"field": "FirstName", "operator": "is_not_empty"},
                {"field": "LastName", "operator": "is_not_empty"},
            ],
            "logic": "AND",
        },
    }
    out = apply_extraction_rules(data, rules, transform_before_filter=True)
    assert len(out) == 1
    assert out[0]["Name"] == "C D"
    assert out[0]["Email"] == "ok@example.com"


def test_align_filter_rules_with_transform():
    schema = ["Email", "FirstName", "LastName"]
    tr = {
        "fields": [
            {
                "action": "merge",
                "source_fields": ["FirstName", "LastName"],
                "output_field": "Name",
            }
        ]
    }
    fr = {
        "conditions": [
            {"field": "email", "operator": "valid_email"},
            {"field": "FirstName", "operator": "is_not_empty"},
        ],
        "logic": "AND",
    }
    aligned = align_filter_rules_with_transform(fr, schema, tr)
    assert len(aligned["conditions"]) == 1
    assert aligned["conditions"][0]["field"] == "Email"
