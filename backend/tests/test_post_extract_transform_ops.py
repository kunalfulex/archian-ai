"""Post-extract data ops must still apply merge/split (not extract-time sanitize)."""
from utils.extraction_profile import ExtractionProfile, sanitize_extraction_rules_for_profile
from utils.extraction_rule_pipeline import apply_extraction_rules


def test_explicit_ops_merge_not_stripped_by_sanitize():
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "source_fields": ["first_name", "last_name"],
                    "action": "merge",
                    "separator": " ",
                    "output_field": "full_name",
                }
            ]
        }
    }
    out = sanitize_extraction_rules_for_profile(
        rules,
        ExtractionProfile.EXPLICIT_OPS,
        source_schema=["first_name", "last_name", "email"],
    )
    assert out["transformation_rules"]["fields"][0]["action"] == "merge"


def test_post_extract_apply_merge_on_source_rows():
    data = [
        {"first_name": "Kunal", "last_name": "Fule", "email": "k@e.com"},
        {"first_name": "Jane", "last_name": "Doe", "email": "j@e.com"},
    ]
    rules = {
        "transformation_rules": {
            "fields": [
                {
                    "source_fields": ["first_name", "last_name"],
                    "action": "merge",
                    "separator": " ",
                    "output_field": "full_name",
                }
            ]
        }
    }
    applied = apply_extraction_rules(data, rules, source_schema=["first_name", "last_name", "email"])
    assert applied[0].get("full_name") == "Kunal Fule"
    assert "first_name" not in applied[0] or applied[0].get("first_name") == "Kunal"
