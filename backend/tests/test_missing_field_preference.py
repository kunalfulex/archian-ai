"""Tests for migration missing-field skip/placeholder."""

from utils.missing_field_preference import apply_missing_field_preference

RULES_SKIP = {"missing_field_preference": {"option": "skip", "placeholderValue": "Unknown"}}


def test_skip_ignores_lookup_supplemental_required_fields():
    """Empty AccountPhone must not skip Contact when AccountName is present."""
    mapping = {
        "AccountName": {
            "object": "Account",
            "field": "Name",
            "lookup_of": "Contact.AccountId",
            "is_required": True,
        },
        "AccountPhone": {
            "object": "Account",
            "field": "Phone",
            "lookup_of": "Contact.AccountId",
            "is_required": True,
        },
        "LastName": {"object": "Contact", "field": "LastName", "is_required": True},
    }
    rows = [
        {
            "AccountName": "Acme",
            "AccountPhone": "",
            "LastName": "Smith",
            "FirstName": "Ann",
            "Email": "a@example.com",
        },
    ]
    out, skipped = apply_missing_field_preference(rows, mapping, RULES_SKIP)
    assert skipped == 0
    assert len(out) == 1


def test_skip_ignores_phantom_sf_field_keys():
    """Synthetic keys from mapping refinement (SF API name, not source column)."""
    mapping = {
        "FirstName": {"object": "Contact", "field": "FirstName", "is_required": False},
        "LastName": {"object": "Contact", "field": "LastName", "is_required": True},
        "Name": {
            "object": "Account",
            "field": "Name",
            "is_required": True,
            "missing": True,
            "value": "Unknown",
        },
    }
    rows = [{"FirstName": "Ann", "LastName": "Smith", "Email": "a@example.com"}]
    out, skipped = apply_missing_field_preference(rows, mapping, RULES_SKIP)
    assert skipped == 0
    assert len(out) == 1


def test_skip_still_drops_rows_missing_primary_required():
    mapping = {
        "LastName": {"object": "Contact", "field": "LastName", "is_required": True},
    }
    rows = [{"FirstName": "Ann", "LastName": ""}]
    out, skipped = apply_missing_field_preference(rows, mapping, RULES_SKIP)
    assert skipped == 1
    assert out == []


def test_skip_ignores_hidden_metadata_keys_on_rows():
    mapping = {
        "filter_rules": {"object": "Contact", "field": "LastName", "is_required": True},
        "LastName": {"object": "Contact", "field": "LastName", "is_required": True},
    }
    rows = [
        {
            "LastName": "Smith",
            "filter_rules": {"conditions": []},
        },
    ]
    out, skipped = apply_missing_field_preference(rows, mapping, RULES_SKIP)
    assert skipped == 0
    assert len(out) == 1
