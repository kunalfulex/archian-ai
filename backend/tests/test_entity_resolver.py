"""Tests for unified plan entity resolution."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services.planning.entity_resolver import (
    _match_salesforce_catalog,
    apply_plan_contract_to_classification,
    resolve_plan_entities,
)
from services.planning.schemas import PlanContract, PlanEntityRef


def test_match_salesforce_catalog_appointments_label():
    catalog = [
        {
            "object_api_name": "ServiceAppointment",
            "label": "Service Appointment",
            "category": "Standard",
        },
        {"object_api_name": "Account", "label": "Account", "category": "Standard"},
    ]
    matched = _match_salesforce_catalog(
        "can you deploy Appointments from SF 1 org to SF 2 org",
        catalog,
        ["Appointments"],
    )
    assert matched is not None
    api, label, _score = matched
    assert api == "ServiceAppointment"
    assert "Appointment" in label


def test_apply_plan_contract_backfills_legacy_fields():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="ServiceAppointment",
            catalog_label="Service Appointment",
            entity_label="Service Appointment",
            resolution_source="catalog_label",
            confidence=0.9,
        ),
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="ServiceAppointment",
            catalog_label="Service Appointment",
            entity_label="Service Appointment",
            resolution_source="destination_same_as_source",
            confidence=0.9,
        ),
    )
    out = apply_plan_contract_to_classification({}, contract)
    assert out["source_object_api_name"] == "ServiceAppointment"
    assert out["destination_object_api_name"] == "ServiceAppointment"
    assert out["destination_same_as_source"] is True


def test_step_entity_label_gsheets_shows_tab_not_spreadsheet_id():
    ref = PlanEntityRef(
        connector_type="google_sheets",
        display_name="Archian Data",
        entity_key="13w05fj34LJg2HlPqEvIHt77pH4JmD2DsFWtun-vImA8::Sheet1",
        catalog_label="Sheet1",
        sheet_name="Sheet1",
        resolution_source="catalog_label",
        confidence=0.95,
    )
    assert ref.step_entity_label() == "Sheet1"
    assert "13w05fj34" not in ref.step_entity_label()


def test_execution_display_shows_api_name_not_semantic_category():
    ref = PlanEntityRef(
        connector_type="salesforce",
        display_name="SF 1",
        entity_key="ServiceAppointment",
        catalog_label="Service Appointment",
        resolution_source="catalog_label",
        confidence=0.9,
    )
    display = ref.execution_display()
    assert "ServiceAppointment" in display
    assert "Appointment Category" not in display
    assert "Service Appointment" in display


def test_rank_salesforce_prefers_service_appointment_over_category():
    from services.planning.entity_resolver import _rank_salesforce_catalog

    catalog = [
        {"object_api_name": "AppointmentCategory", "label": "Appointment Category"},
        {"object_api_name": "ServiceAppointment", "label": "Service Appointment"},
    ]
    ranked = _rank_salesforce_catalog(
        "deploy Appointments from SF 1 to SF 2",
        catalog,
        ["Appointments"],
    )
    assert ranked
    assert ranked[0][0] == "ServiceAppointment"


def test_resolve_plan_entities_same_entity_sf_migration():
    async def _run():
        classification = {
            "source": "salesforce",
            "source_name": "SF 1",
            "source_connector_id": "tok-src",
            "destination": "salesforce",
            "destination_name": "SF 2",
            "destination_connector_id": "tok-dest",
            "destination_object_api_name": "ServiceAppointment",
        }
        parsed_goal = {
            "entities": ["Appointments"],
            "destination_same_as_source": True,
        }
        classification["destination_same_as_source"] = True

        async def fake_side(*, user_id, classification, role, prompt, parsed_goal, migration_intent=None):
            del user_id, prompt, parsed_goal, migration_intent
            if role == "destination":
                return PlanEntityRef(
                    connector_type="salesforce",
                    display_name=classification.get("destination_name"),
                    entity_key="ServiceAppointment",
                    entity_label="Service Appointment",
                    resolution_source="user_explicit",
                    confidence=0.95,
                )
            return PlanEntityRef(
                connector_type="salesforce",
                display_name=classification.get("source_name"),
                resolution_source="pending",
            )

        with patch(
            "services.planning.entity_resolver._resolve_side_entity",
            side_effect=fake_side,
        ):
            return await resolve_plan_entities(
                classification,
                user_id="user-1",
                prompt="deploy Appointments from SF 1 to SF 2",
                parsed_goal=parsed_goal,
            )

    contract = asyncio.run(_run())
    assert contract.destination.entity_key == "ServiceAppointment"
    assert contract.source.entity_key == "ServiceAppointment"
    assert contract.source.resolution_source == "destination_same_as_source"


def test_resolve_plan_entities_does_not_copy_dest_to_source_without_same_endpoint_flag():
    async def _run():
        classification = {
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "salesforce",
            "destination_name": "SF 2",
            "destination_object_api_name": "RecordType",
            "destination_same_as_source": False,
        }

        async def fake_side(*, user_id, classification, role, prompt, parsed_goal, migration_intent=None):
            del user_id, prompt, parsed_goal, migration_intent
            if role == "destination":
                return PlanEntityRef(
                    connector_type="salesforce",
                    entity_key="RecordType",
                    entity_label="Record Types",
                    resolution_source="catalog_label",
                    confidence=0.9,
                )
            return PlanEntityRef(
                connector_type="salesforce",
                resolution_source="pending",
            )

        with patch(
            "services.planning.entity_resolver._resolve_side_entity",
            side_effect=fake_side,
        ):
            return await resolve_plan_entities(
                classification,
                user_id="user-1",
                prompt="deploy user data to salesforce",
                parsed_goal={"destination_same_as_source": False},
            )

    contract = asyncio.run(_run())
    assert contract.destination.entity_key == "RecordType"
    assert not contract.source.is_resolved()
