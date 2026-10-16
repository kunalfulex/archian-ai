"""Plan contract destination sync on continuation destination change (RESOLVE)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.intent.continuation_planning import plan_from_job_context
from services.intent.job_context import JobContext, ResourcePin
from services.intent.session_continuation import SessionContinuationDecision
from services.planning.continuation_destination import enrich_continuation_destination_connectors
from services.planning.continuation_policy import (
    apply_continuation_endpoint_pins_from_job,
    clear_stale_plan_contract_destination,
    plan_contract_destination_stale_vs_classification,
    sync_plan_contract_destination_from_classification,
)
from services.planning.connector_llm_resolver import LlmConnectorPick
from services.planning.entity_resolver import apply_plan_contract_to_classification
from services.planning.google_sheets_destination import workbook_name_hint_from_destination_phrase
from services.planning.resource_catalog import (
    GoogleSheetRef,
    GoogleSheetsConnectionRef,
    ResourceCatalog,
)
from services.planning.schemas import PlanContract, PlanEntityRef


def test_clear_stale_plan_contract_destination():
    cls = {
        "plan_contract": {
            "source": {"connector_type": "file"},
            "destination": {
                "connector_type": "salesforce",
                "display_name": "SF 1",
                "entity_key": "Contact",
                "resolution_source": "plan_pinned",
            },
        }
    }
    out = clear_stale_plan_contract_destination(cls)
    assert "destination" not in (out.get("plan_contract") or {})


def test_apply_endpoint_pins_clears_contract_on_new_destination():
    job = JobContext(
        job_id="job-1",
        mapping_ready=True,
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
        ),
    )
    cls = {
        "destination": "salesforce",
        "destination_name": "SF 1",
        "destination_object_api_name": "Contact",
        "planner_destination_entity_confirmed": True,
        "plan_contract": {
            "destination": {
                "connector_type": "salesforce",
                "display_name": "SF 1",
                "entity_key": "Contact",
                "resolution_source": "plan_pinned",
            }
        },
    }
    goal = {"destination_phrase": "contact list workbook of xTag Account"}
    out = apply_continuation_endpoint_pins_from_job(cls, goal, job)
    assert out.get("destination") is None
    assert out.get("destination_object_api_name") is None
    assert "destination" not in (out.get("plan_contract") or {})


def test_plan_contract_stale_vs_classification():
    contract = PlanContract(
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            resolution_source="plan_pinned",
            confidence=0.9,
        )
    )
    cls = {"destination": "google_sheets", "_destination_connector_validated": True}
    assert plan_contract_destination_stale_vs_classification(cls, contract)


def test_workbook_hint_from_phrase():
    cat = ResourceCatalog(
        google_sheet_rows=[
            GoogleSheetRef(spreadsheet_name="contact list", sheet_name="Sheet1"),
        ]
    )
    hint = workbook_name_hint_from_destination_phrase(
        "contact list workbook of xTag Account",
        account_display_name="xTag Account",
        catalog=cat,
    )
    assert hint == "contact list"


def test_apply_plan_contract_skips_stale_job_salesforce():
    contract = PlanContract(
        destination=PlanEntityRef(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
            resolution_source="plan_pinned",
            confidence=0.9,
        )
    )
    cls = {
        "destination": "google_sheets",
        "destination_account_name": "xTag Account",
        "destination_connector_id": "conn-xtag",
        "google_sheets_connection_id": "conn-xtag",
        "_destination_connector_validated": True,
    }
    out = apply_plan_contract_to_classification(cls, contract)
    assert out["destination"] == "google_sheets"
    assert out.get("destination_name") != "SF 1"
    assert (out.get("plan_contract") or {}).get("destination", {}).get("connector_type") == (
        "google_sheets"
    )


def test_enrich_resolves_workbook_and_syncs_contract():
    async def _run():
        job = JobContext(
            job_id="job-mapped",
            mapping_ready=True,
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="SF 1",
                entity_key="Contact",
                connector_id="sf-1",
            ),
            source=ResourcePin(connector_type="file", display_name="contacts.xlsx"),
        )
        cat = ResourceCatalog(
            has_google_sheets=True,
            google_sheets_connections=[
                GoogleSheetsConnectionRef(connection_id="conn-xtag", name="xTag Account"),
            ],
            google_sheet_rows=[
                GoogleSheetRef(
                    spreadsheet_name="contact list",
                    sheet_name="Sheet1",
                    spreadsheet_id="sheet-cl-1",
                ),
            ],
        )
        cls = {
            "_continuation_steps": ["map", "migrate"],
            "plan_contract": {
                "destination": {
                    "connector_type": "salesforce",
                    "display_name": "SF 1",
                    "entity_key": "Contact",
                    "resolution_source": "plan_pinned",
                }
            },
        }
        goal = {
            "operations": ["map", "migrate"],
            "destination_phrase": "contact list workbook of xTag Account",
        }
        pick = LlmConnectorPick("google_sheets", "xTag Account", "conn-xtag", 0.9)
        db_match = {
            "found": True,
            "spreadsheet_id": "sheet-cl-1",
            "spreadsheet_name": "contact list",
            "sheet_name": "Sheet1",
            "google_sheets_connection_id": "conn-xtag",
        }
        with patch(
            "services.planning.connector_llm_resolver.llm_resolve_destination_connector",
            new=AsyncMock(return_value=pick),
        ), patch(
            "services.google_sheets_connection_service.resolve_workbook_by_name",
            new=AsyncMock(return_value=db_match),
        ):
            out = await enrich_continuation_destination_connectors(
                cls,
                prompt="migrate to contact list workbook of xTag Account",
                catalog=cat,
                goal=goal,
                job=job,
                user_id="user-1",
            )
        assert out["destination"] == "google_sheets"
        assert out.get("destination_name") == "contact list"
        contract_dest = (out.get("plan_contract") or {}).get("destination") or {}
        assert contract_dest.get("connector_type") == "google_sheets"
        assert contract_dest.get("display_name") == "contact list"
        assert contract_dest.get("entity_key") == "sheet-cl-1::Sheet1"

    asyncio.run(_run())


def test_sync_plan_contract_from_classification_pins():
    cls = {
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_connector_id": "conn-xtag",
        "destination_spreadsheet_id": "sheet-cl-1",
        "destination_sheet_name": "Sheet1",
        "destination_object_api_name": "sheet-cl-1::Sheet1",
    }
    out = sync_plan_contract_destination_from_classification(cls)
    dest = (out.get("plan_contract") or {}).get("destination") or {}
    assert dest.get("connector_type") == "google_sheets"
    assert dest.get("entity_key") == "sheet-cl-1::Sheet1"
