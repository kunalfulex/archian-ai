"""Salesforce extract key alignment and LLM mapping repair."""
from utils.mapping_utils import (
    flat_data_uses_salesforce_extract_keys,
    repair_salesforce_mapping_to_extract_keys,
)


def test_flat_data_uses_salesforce_extract_keys():
    assert flat_data_uses_salesforce_extract_keys(
        {"FirstName": "Rose", "LastName": "G", "Email": "a@b.com", "AccountName": "Acme"}
    )
    assert not flat_data_uses_salesforce_extract_keys(
        {"first_name": "Rose", "last_name": "G", "company": "Acme"}
    )


def test_repair_company_and_phone_hijack():
    flat = {
        "FirstName": "Rose",
        "LastName": "Gonzalez",
        "Email": "rose@edge.com",
        "Phone": "+15127576000",
        "AccountName": "Edge Communications",
        "AccountPhone": "5127576000",
    }
    llm_mapping = {
        "Email": {"object": "Contact", "field": "Email"},
        "FirstName": {"object": "Contact", "field": "FirstName"},
        "LastName": {"object": "Contact", "field": "LastName"},
        "company": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
        "Phone": {
            "object": "Account",
            "field": "Phone",
            "lookup_of": "Contact.AccountId",
        },
    }
    fixed = repair_salesforce_mapping_to_extract_keys(llm_mapping, flat)
    assert "company" not in fixed
    assert fixed["AccountName"]["object"] == "Account"
    assert fixed["AccountPhone"]["object"] == "Account"
    assert fixed["Phone"]["object"] == "Contact"
    assert fixed["Phone"]["field"] == "Phone"
