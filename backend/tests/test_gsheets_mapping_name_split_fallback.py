"""Google Sheets mapping must auto-split compound Name to First/Last columns."""

from utils.mapping_transform_context import (
    apply_post_llm_transform_reconciliation,
    classify_gsheets_destination_name_shape,
)


def test_gsheets_name_split_fallback_when_mapped_to_first_only():
    mapping = {
        "Name": {
            "sheet": "Sheet1",
            "column": "First Name",
            "value": "Aanya Ahmad",
            "is_duplicate_check": False,
        },
        "Email": {
            "sheet": "Sheet1",
            "column": "Email",
            "value": "a@b.com",
            "is_duplicate_check": True,
        },
    }
    sheets_structure = {
        "Sheet1": {
            "columns": {
                "First Name": {"data_type": "text"},
                "Last Name": {"data_type": "text"},
                "Email": {"data_type": "text"},
            }
        }
    }
    transformation_rules = {"fields": []}
    enhanced_mapping = {}
    dest_name_shape = classify_gsheets_destination_name_shape(sheets_structure)

    reconciled = apply_post_llm_transform_reconciliation(
        mapping=mapping,
        flat_data={"Name": "Aanya Ahmad", "Email": "a@b.com"},
        destination_name_shape=dest_name_shape,
        destination_type="google_sheets",
        transformation_rules=transformation_rules,
        enhanced_mapping=enhanced_mapping,
        sample_data=[{"Name": "Aanya Ahmad", "Email": "a@b.com"}],
        base_container="Sheet1",
    )
    enhanced_mapping = reconciled.get("enhanced_mapping") or {}
    transformation_rules = reconciled.get("transformation_rules") or {"fields": []}

    assert "Name" in enhanced_mapping
    assert isinstance(enhanced_mapping["Name"], list)
    assert len(enhanced_mapping["Name"]) == 2
    assert enhanced_mapping["Name"][0]["column"] == "First Name"
    assert enhanced_mapping["Name"][1]["column"] == "Last Name"
    split_rules = [r for r in transformation_rules["fields"] if r.get("action") == "split_2"]
    assert len(split_rules) == 1
    assert split_rules[0]["source_field"] == "Name"
