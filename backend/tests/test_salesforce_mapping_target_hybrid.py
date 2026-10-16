"""Tests for hybrid Salesforce mapping destination resolution."""

import pytest

from services.mapping_plan.target_resolution import resolve_mapping_target

SERVICE_APPOINTMENTS_MIGRATE_PROMPT = (
    "Extract the 100 records from the Service Appointments sheet, combine DEALERCODE VC, "
    "DEALERBRANCHCODE VC, CUSTCODE VC column as Customer Account, and combine DEALERCODE VC, "
    "DEALERBRANCHCODE VC, CRONO VC as CRO User and migrate it to the Service Appointment "
    "object of the SF 1 Salesforce org"
)


def test_classification_snapshot_preferred_over_heuristics():
    """Full pipeline should use planner/classifier object, not regex guesses."""
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction=SERVICE_APPOINTMENTS_MIGRATE_PROMPT,
        classification_snapshot={"destination_object_api_name": "ServiceAppointment"},
    )
    assert resolved.target_object_api_name == "ServiceAppointment"
    assert resolved.resolution_source == "classification_explicit"


def test_no_pascalcase_the_service_appointment_bug():
    """Regression: sync path must not invent bogus API names; defer to async LLM."""
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction=SERVICE_APPOINTMENTS_MIGRATE_PROMPT,
        classification_snapshot={},
    )
    assert resolved.target_object_api_name != "TheServiceAppointment"
    assert resolved.target_object_api_name is None


def test_salesforce_snapshot_object_used_when_prompt_missing():
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction="start mapping to SF 1",
        classification_snapshot={"destination_object_api_name": "Contact"},
    )
    assert resolved.target_object_api_name == "Contact"
    assert resolved.resolution_source == "classification_explicit"
