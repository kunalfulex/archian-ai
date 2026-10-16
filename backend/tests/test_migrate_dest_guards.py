"""Tests for external-migrate spreadsheet create guards."""

from services.planning.connector_guards import (
    apply_migrate_destination_guards,
    blocks_spreadsheet_create_for_route,
    migrate_to_external_destination_requested,
)

MIGRATE_PROMPT = (
    "Extract 100 records from Service Appointments and migrate to SF 1 Service Appointment"
)


def test_migrate_to_external_detected():
    classification = {
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    goal = {"operations": ["extract", "migrate"]}
    assert migrate_to_external_destination_requested(classification, MIGRATE_PROMPT, goal)


def test_blocks_create_without_explicit_new_sheet():
    classification = {
        "destination": "salesforce",
        "destination_name": "SF 1",
        "planner_create_spreadsheet": True,
    }
    goal = {"create_destination": True, "operations": ["extract", "migrate"]}
    assert blocks_spreadsheet_create_for_route(classification, goal, MIGRATE_PROMPT)


def test_does_not_block_when_user_creates_sheet():
    classification = {
        "destination": "google_sheets",
        "destination_name": "CRM list",
    }
    goal = {"create_destination": True}
    prompt = "extract contacts and create new google sheet named CRM list"
    assert not blocks_spreadsheet_create_for_route(classification, goal, prompt)


def test_apply_clears_flags():
    out, goal = apply_migrate_destination_guards(
        {"destination": "salesforce", "destination_name": "SF 1", "planner_create_spreadsheet": True},
        {"create_destination": True, "operations": ["extract", "migrate"]},
        MIGRATE_PROMPT,
    )
    assert out["planner_create_spreadsheet"] is False
    assert goal["create_destination"] is False
