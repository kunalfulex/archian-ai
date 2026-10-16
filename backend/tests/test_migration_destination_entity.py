"""Tests for destination entity API name vs catalog document label resolution."""

from utils.migration_destination_entity import (
    coerce_destination_entity_api_name,
    infer_salesforce_object_from_mapping,
    looks_like_catalog_document_label,
)


def test_looks_like_catalog_document_label():
    assert looks_like_catalog_document_label("Salesforce Contact Data") is True
    assert looks_like_catalog_document_label("Contact") is False
    assert looks_like_catalog_document_label("user_plans") is False


def test_infer_salesforce_object_from_mapping_majority():
    mapping = {
        "FirstName": {"object": "Contact", "field": "FirstName"},
        "LastName": {"object": "Contact", "field": "LastName"},
        "Company": {"object": "Account", "field": "Name", "lookup_of": "Contact.AccountId"},
    }
    assert infer_salesforce_object_from_mapping(mapping) == "Contact"


def test_coerce_rejects_catalog_label_and_uses_mapping():
    mapping = {
        "Email": {"object": "Contact", "field": "Email"},
    }
    assert (
        coerce_destination_entity_api_name(
            "Salesforce Contact Data",
            connector_type="salesforce",
            mapping=mapping,
        )
        == "Contact"
    )


def test_coerce_preserves_valid_api_name():
    assert (
        coerce_destination_entity_api_name(
            "Contact",
            connector_type="salesforce",
            mapping={},
        )
        == "Contact"
    )
