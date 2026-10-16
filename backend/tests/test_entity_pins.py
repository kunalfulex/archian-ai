"""Tests for connector-symmetric entity pin helpers."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.planning.entity_pins import (
    pin_destination_from_classification,
    pinned_entity_key,
    plan_entity_ref_from_pin,
    write_pinned_entity_key,
)
from services.planning.entity_resolver import apply_plan_contract_to_classification
from services.planning.schemas import PlanContract, PlanEntityRef


def test_pinned_entity_key_sql_destination():
    cls = {
        "destination": "sql",
        "destination_table_name": "user",
        "planner_destination_entity_confirmed": True,
    }
    assert pinned_entity_key(cls, role="destination") == "user"


def test_pinned_entity_key_salesforce_destination():
    cls = {
        "destination": "salesforce",
        "destination_object_api_name": "Contact",
    }
    assert pinned_entity_key(cls, role="destination") == "Contact"


def test_pinned_entity_key_source_sql():
    cls = {"source": "sql", "source_table_name": "users"}
    assert pinned_entity_key(cls, role="source") == "users"


def test_pin_destination_from_classification():
    cls = {
        "destination": "sql",
        "destination_name": "Test DB",
        "destination_table_name": "user",
        "planner_destination_entity_confirmed": True,
    }
    ref = pin_destination_from_classification(cls)
    assert ref is not None
    assert ref.entity_key == "user"
    assert ref.resolution_source == "user_explicit"


def test_write_pinned_entity_key_sql():
    out = write_pinned_entity_key(
        {},
        role="destination",
        connector_type="sql",
        entity_key="orders",
    )
    assert out["destination_table_name"] == "orders"


def test_apply_plan_contract_honors_confirmed_sql_dest():
    cls = {
        "destination": "sql",
        "destination_table_name": "user",
        "planner_destination_entity_confirmed": True,
    }
    contract = PlanContract(
        source=PlanEntityRef(connector_type="salesforce", entity_key="Contact"),
        destination=PlanEntityRef(
            connector_type="sql",
            entity_key="migration_records",
            resolution_source="llm_bind",
            confidence=0.8,
        ),
    )
    out = apply_plan_contract_to_classification(cls, contract)
    assert out["destination_table_name"] == "user"
    pc = out["plan_contract"]
    assert pc["destination"]["entity_key"] == "user"
    assert pc["destination"]["resolution_source"] == "user_explicit"


def test_plan_entity_ref_from_pin_gsheets():
    cls = {
        "destination": "google_sheets",
        "destination_name": "My Sheet",
        "destination_spreadsheet_id": "abc123",
    }
    ref = plan_entity_ref_from_pin(
        cls, role="destination", entity_key="abc123::Contacts"
    )
    assert ref.entity_key == "abc123::Contacts"
    assert ref.sheet_name == "Contacts"
    assert ref.spreadsheet_id == "abc123"


def test_retrieve_destination_sql_shortlist_has_fields():
    from services.planning.binding.retrieve import retrieve_entity_shortlist

    classification = {
        "source": "salesforce",
        "source_name": "SF1",
        "destination": "sql",
        "destination_name": "Test DB",
        "destination_connector_id": "conn-1",
    }

    async def _run():
        with patch(
            "services.planning.catalog_grounding.load_side_catalog_summary",
            new_callable=AsyncMock,
            return_value=(
                [
                    {"entity_key": "user", "api_name": "user", "label": "user"},
                    {"entity_key": "orders", "api_name": "orders", "label": "orders"},
                ],
                "sql",
                {"connector_id": "conn-1"},
            ),
        ), patch(
            "services.planning.extract_field_clarification.load_field_entries",
            new_callable=AsyncMock,
            side_effect=lambda _uid, _cls, entity_key, role="source": [
                {"api_name": "id", "label": "id"},
                {"api_name": "email", "label": "email"},
            ]
            if entity_key == "user"
            else [{"api_name": "name", "label": "name"}],
        ), patch(
            "services.planning.binding.retrieve.resolve_connector_instance_id",
            new_callable=AsyncMock,
            return_value="inst-1",
        ), patch(
            "services.planning.binding.retrieve.fetch_semantics_map",
            new_callable=AsyncMock,
            return_value={},
        ):
            candidates, ct, _ = await retrieve_entity_shortlist(
                "user-1",
                classification,
                role="destination",
                prompt="migrate contact to test db user table",
            )
            return candidates, ct

    candidates, ct = asyncio.run(_run())
    assert ct == "sql"
    assert len(candidates) >= 1
    assert any(c.entity_key == "user" for c in candidates)
    user_cand = next(c for c in candidates if c.entity_key == "user")
    assert len(user_cand.fields) >= 1
