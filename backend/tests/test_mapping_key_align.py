"""Mapping keys must match extraction column names for UI + bulk migrate."""
from utils.mapping_utils import (
    align_mapping_keys_to_extracted_data,
    record_value_for_mapping_key,
    resolve_extracted_field_key,
)


def test_resolve_snake_case_to_salesforce_api_names():
    keys = ["Email", "FirstName", "LastName", "Id"]
    assert resolve_extracted_field_key("email", keys) == "Email"
    assert resolve_extracted_field_key("first_name", keys) == "FirstName"
    assert resolve_extracted_field_key("last_name", keys) == "LastName"


def test_align_mapping_keys_rekeys_llm_output():
    flat = {"Email": "a@b.com", "FirstName": "Rose", "LastName": "Gonzalez"}
    mapping = {
        "email": {
            "object": "Contact",
            "field": "Email",
            "value": "rose@edge.com",
            "is_required": True,
        },
        "first_name": {"object": "Contact", "field": "FirstName", "value": "Rose"},
        "last_name": {
            "object": "Contact",
            "field": "LastName",
            "value": "Gonzalez",
            "is_required": True,
        },
    }
    aligned = align_mapping_keys_to_extracted_data(mapping, flat)
    assert set(aligned.keys()) == {"Email", "FirstName", "LastName"}
    assert aligned["Email"]["field"] == "Email"
    assert record_value_for_mapping_key(flat, "email") == "a@b.com"


def test_record_value_for_mapping_key_case_insensitive():
    record = {"LastName": "Smith"}
    assert record_value_for_mapping_key(record, "last_name") == "Smith"
