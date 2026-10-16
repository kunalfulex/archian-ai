"""Schema alignment for post-extract transform rules."""
from utils.extraction_rules_validate import (
    align_transform_rules_with_schema,
    resolve_field_in_schema,
)
from utils.rule_accumulation import apply_transformation_rules_in_order


def test_resolve_field_in_schema_fuzzy():
    schema = ["Email", "FullName"]
    assert resolve_field_in_schema("full name", schema) == "FullName"
    assert resolve_field_in_schema("FULLNAME", schema) == "FullName"


def test_align_split_drops_hallucinated_source():
    delta = {
        "fields": [
            {
                "action": "split_2",
                "source_field": "FirstName",
                "output_fields": ["FirstName", "LastName"],
            }
        ]
    }
    aligned = align_transform_rules_with_schema(delta, ["Email", "FullName"])
    fields = aligned.get("fields") if isinstance(aligned, dict) else aligned
    assert fields == []


def test_align_split_keeps_resolvable_source():
    delta = {
        "fields": [
            {
                "action": "split_2",
                "source_field": "Full Name",
                "output_fields": ["FirstName", "LastName"],
                "split_char": " ",
            }
        ]
    }
    aligned = align_transform_rules_with_schema(delta, ["Email", "FullName"])
    fields = aligned.get("fields") if isinstance(aligned, dict) else aligned
    assert len(fields) == 1
    assert fields[0]["source_field"] == "FullName"


def test_apply_split_on_fullname_column():
    data = [{"Email": "a@b.co", "FullName": "Rose Gonzalez"}]
    rules = {
        "fields": [
            {
                "action": "split_2",
                "source_field": "FullName",
                "output_fields": ["FirstName", "LastName"],
                "split_char": " ",
            }
        ]
    }
    out = apply_transformation_rules_in_order(data, rules, ["Email", "FullName"])
    assert out[0]["FirstName"] == "Rose"
    assert out[0]["LastName"] == "Gonzalez"
    assert "FullName" not in out[0]
