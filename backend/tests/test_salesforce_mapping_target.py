"""Tests for catalog-driven Salesforce destination object resolution."""

from services.mapping_plan.catalog_target_resolver import (
    _match_prompt_to_salesforce_catalog_object,
)


_CATALOG = [
    {"object_api_name": "Account", "label": "Account"},
    {"object_api_name": "Contact", "label": "Contact"},
    {"object_api_name": "ServiceAppointment", "label": "Service Appointment"},
]


def test_catalog_match_account_object_suffix():
    matched = _match_prompt_to_salesforce_catalog_object(
        "start mapping to account object",
        _CATALOG,
    )
    assert matched == "Account"


def test_catalog_match_contact_with_org_prefix():
    matched = _match_prompt_to_salesforce_catalog_object(
        "map it to production 2 contact object",
        _CATALOG,
    )
    assert matched == "Contact"


def test_catalog_match_service_appointment_label():
    matched = _match_prompt_to_salesforce_catalog_object(
        "migrate it to the Service Appointment object of the SF 1 Salesforce org",
        _CATALOG,
    )
    assert matched == "ServiceAppointment"


def test_catalog_match_none_without_map_context():
    assert (
        _match_prompt_to_salesforce_catalog_object(
            "filter rows where email is blank",
            _CATALOG,
        )
        is None
    )


def test_catalog_match_none_for_unknown_object():
    assert (
        _match_prompt_to_salesforce_catalog_object(
            "map to CustomWidget object",
            _CATALOG,
        )
        is None
    )
