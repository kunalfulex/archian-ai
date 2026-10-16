"""Tests for Salesforce lookup_of sanitization against base-object lookup metadata."""

from utils.sf_mapping_lookup_sanitize import (
    build_base_lookup_index,
    parse_lookup_of_reference,
    sanitize_salesforce_mapping_lookup_of,
    should_drop_salesforce_lookup_of,
    strip_empty_lookup_of_from_mapping,
)


def _contact_account_structure() -> dict:
    return {
        "Contact": {
            "fields": {
                "AccountId": {
                    "is_lookup": True,
                    "lookup_object": "Account",
                },
                "OwnerId": {
                    "is_lookup": True,
                    "lookup_object": "User",
                },
                "FirstName": {"is_lookup": False},
            }
        },
        "Account": {
            "fields": {
                "Name": {"is_lookup": False},
                "Phone": {"is_lookup": False},
                "ParentId": {
                    "is_lookup": True,
                    "lookup_object": "Account",
                },
            }
        },
    }


def test_drop_lookup_of_on_primary_base_object_field():
    drop, reason = should_drop_salesforce_lookup_of(
        "Account",
        "Contact.AccountId",
        base_objects=["Account"],
        objects_structure=_contact_account_structure(),
    )
    assert drop is True
    assert reason == "primary_object_direct_field"


def test_keep_valid_contact_to_account_lookup():
    drop, reason = should_drop_salesforce_lookup_of(
        "Account",
        "Contact.AccountId",
        base_objects=["Contact"],
        objects_structure=_contact_account_structure(),
    )
    assert drop is False
    assert reason == ""


def test_drop_wrong_lookup_field_target():
    drop, reason = should_drop_salesforce_lookup_of(
        "Account",
        "Contact.OwnerId",
        base_objects=["Contact"],
        objects_structure=_contact_account_structure(),
    )
    assert drop is True
    assert reason == "lookup_target_mismatch"


def test_drop_lookup_base_not_migration_base():
    drop, reason = should_drop_salesforce_lookup_of(
        "Account",
        "Contact.AccountId",
        base_objects=["Account"],
        objects_structure=_contact_account_structure(),
    )
    assert drop is True
    assert reason == "primary_object_direct_field"


def test_sanitize_contact_extract_to_account_destination():
    """Regression: Contact fields mapped to Account must not keep Contact.AccountId."""
    mapping = {
        "FirstName": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
        "LastName": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
        "Phone": {"object": "Account", "field": "Phone"},
    }
    out, dropped = sanitize_salesforce_mapping_lookup_of(
        mapping,
        _contact_account_structure(),
        base_objects=["Account"],
    )
    assert "lookup_of" not in out["FirstName"]
    assert "lookup_of" not in out["LastName"]
    assert len(dropped) == 2
    assert out["Phone"]["field"] == "Phone"


def test_sanitize_preserves_valid_related_account_on_contact_base():
    mapping = {
        "company": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
        },
        "first_name": {"object": "Contact", "field": "FirstName"},
    }
    out, dropped = sanitize_salesforce_mapping_lookup_of(
        mapping,
        _contact_account_structure(),
        base_objects=["Contact"],
    )
    assert out["company"]["lookup_of"] == "Contact.AccountId"
    assert dropped == []


def test_build_index_from_base_objects_only():
    valid, targets = build_base_lookup_index(
        _contact_account_structure(),
        ["Contact"],
    )
    assert "Contact.AccountId" in valid
    assert targets["Contact.AccountId"] == "Account"
    assert "Account.ParentId" not in valid


def test_parse_lookup_of_reference_rejects_empty_and_malformed():
    assert parse_lookup_of_reference("") is None
    assert parse_lookup_of_reference("   ") is None
    assert parse_lookup_of_reference("Contact") is None
    assert parse_lookup_of_reference(".AccountId") is None
    assert parse_lookup_of_reference("Contact.AccountId") == ("Contact", "AccountId")


def test_strip_empty_lookup_of_from_mapping():
    mapping = {
        "FirstName": {"object": "Contact", "field": "FirstName", "lookup_of": ""},
        "LastName": {"object": "Contact", "field": "LastName", "lookup_of": "Contact.AccountId"},
        "FullName": [
            {"object": "Contact", "field": "FirstName", "lookup_of": ""},
            {"object": "Contact", "field": "LastName", "lookup_of": " Contact.AccountId "},
        ],
    }
    out = strip_empty_lookup_of_from_mapping(mapping)
    assert "lookup_of" not in out["FirstName"]
    assert out["LastName"]["lookup_of"] == "Contact.AccountId"
    assert "lookup_of" not in out["FullName"][0]
    assert out["FullName"][1]["lookup_of"] == "Contact.AccountId"
