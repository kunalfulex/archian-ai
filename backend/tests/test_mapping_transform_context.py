"""Tests for mapping transform context (programmatic combine, dest-shaped skip)."""
from services.data_transformation_service import DataTransformationService
from utils.mapping_transform_context import (
    extraction_schema_looks_destination_shaped,
    source_fields_from_flat_data,
    try_programmatic_name_combine_for_mapping,
)


def test_source_fields_from_flat_data():
    flat = {"first_name": "A", "email": "a@b.com", "filter_rules": {"x": 1}}
    assert source_fields_from_flat_data(flat) == ["email", "first_name"]


def test_destination_shaped_schema_detected():
    assert extraction_schema_looks_destination_shaped({"Name": "Doe", "Email": "a@b.com"})
    assert not extraction_schema_looks_destination_shaped(
        {"first_name": "A", "last_name": "B", "email": "a@b.com"}
    )


def test_programmatic_combine_when_already_mapped_same_column():
    sheets_shape = {
        "Customer Contact Details": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        }
    }
    flat = {"first_name": "Kunal", "last_name": "Fule", "email": "k@e.com"}
    mapping = {
        "first_name": {"column": "Name", "sheet": "Customer Contact Details"},
        "last_name": {"column": "Name", "sheet": "Customer Contact Details"},
        "email": {"column": "Email", "sheet": "Customer Contact Details"},
    }
    out = try_programmatic_name_combine_for_mapping(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=sheets_shape,
        destination_type="google_sheets",
        base_container="Customer Contact Details",
    )
    assert out and out.get("skip_llm")
    assert out["enhanced_mapping"]["first_name"].get("is_combined_field") is True
    assert len(out["transformation_rules"]["fields"]) == 1


def test_programmatic_combine_first_last_to_name_column_requires_base_hint():
    sheets_shape = {
        "Customer Contact Details": {
            "generic_name_columns": ["Name"],
            "has_separate_first_last": False,
        }
    }
    flat = {"first_name": "Kunal", "last_name": "Fule", "email": "k@e.com"}
    mapping = {
        "email": {"column": "Email", "sheet": "Customer Contact Details"},
    }
    out = try_programmatic_name_combine_for_mapping(
        flat_data=flat,
        mapping=mapping,
        destination_name_shape=sheets_shape,
        destination_type="google_sheets",
        base_container="Customer Contact Details",
    )
    assert out and out.get("skip_llm")
    fields = out["transformation_rules"]["fields"]
    assert fields[0]["action"] == "combine"
    assert set(fields[0]["source_fields"]) == {"first_name", "last_name"}
    assert "first_name" in out["enhanced_mapping"]
    assert out["enhanced_mapping"]["first_name"]["column"] == "Name"


def test_transform_mapping_keeps_combine_output_and_other_fields():
    mapping = {
        "first_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True},
        "last_name": {"sheet": "Sheet1", "column": "Name", "is_combined_field": True},
        "phone": {"sheet": "Sheet1", "column": "Mobile Number"},
    }
    rules = {
        "fields": [
            {
                "action": "combine",
                "source_fields": ["first_name", "last_name"],
                "output_field": "Name",
                "output_sheet": "Sheet1",
                "separator": " ",
            }
        ]
    }
    out = DataTransformationService.transform_mapping_for_transformed_data(
        mapping=mapping,
        transformation_rules=rules,
        destination="google_sheets",
    )
    assert "Name" in out
    assert out["Name"]["column"] == "Name"
    assert "phone" in out
    assert out["phone"]["column"] == "Mobile Number"


def test_transform_mapping_preserves_lookup_of_on_split():
    mapping = {
        "Name": [
            {
                "object": "Contact",
                "field": "FirstName",
                "lookup_of": "Contact.AccountId",
                "is_split_field": True,
            },
            {
                "object": "Contact",
                "field": "LastName",
                "lookup_of": "Contact.AccountId",
                "is_split_field": True,
            },
        ],
    }
    rules = {
        "fields": [
            {"action": "split_2", "source_field": "Name", "output_fields": ["FirstName", "LastName"]}
        ]
    }
    out = DataTransformationService.transform_mapping_for_transformed_data(
        mapping=mapping,
        transformation_rules=rules,
        destination="salesforce",
    )
    assert out["FirstName"]["lookup_of"] == "Contact.AccountId"
    assert out["LastName"]["lookup_of"] == "Contact.AccountId"
    assert "is_split_field" not in out["FirstName"]
