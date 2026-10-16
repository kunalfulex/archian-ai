"""Tests for plan binding guards and ensure helper."""
from __future__ import annotations

from services.planning.binding.guards import (
    plan_binding_needed,
    should_skip_source_entity_resolver,
    source_resolved_by_binding,
)
from services.planning.schemas import PlanContract, PlanEntityRef


def test_source_resolved_by_binding_llm_bind():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    assert source_resolved_by_binding(contract) is True
    assert should_skip_source_entity_resolver(contract) is True


def test_plan_binding_not_needed_when_source_bound():
    contract = PlanContract(
        source=PlanEntityRef(
            connector_type="sql",
            entity_key="users",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )
    assert (
        plan_binding_needed(
            {"plan_route": "fresh_path", "source": "sql"},
            {"operations": ["extract"]},
            contract,
        )
        is False
    )


def test_plan_binding_needed_when_no_contract():
    assert (
        plan_binding_needed(
            {"plan_route": "fresh_path", "source": "sql", "_pipeline_tokens": ["extract"]},
            {"operations": ["extract"]},
            None,
        )
        is True
    )


def test_plan_binding_not_needed_for_create_only():
    from services.planning.create_metadata import apply_create_metadata_to_classification
    from services.planning.plan_scope import sync_scope_into_classification

    goal = {
        "operations": ["create"],
        "create_destination": True,
        "spreadsheet_title": "Archian Data",
        "spreadsheet_headers": ["Name", "Email"],
    }
    cls = apply_create_metadata_to_classification(
        {
            "plan_route": "fresh_path",
            "destination": "google_sheets",
            "intent": "create",
            "operations": ["create"],
        },
        goal,
    )
    cls = sync_scope_into_classification(cls, goal, ["create"])
    assert (
        plan_binding_needed(cls, goal, None)
        is False
    )


def test_plan_binding_needed_when_stale_job_contract_destination():
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
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "destination": "google_sheets",
        "destination_connector_id": "conn-gs",
        "_destination_connector_validated": True,
        "_pipeline_tokens": ["map", "migrate"],
        "operations": ["map", "migrate"],
    }
    goal = {"operations": ["map", "migrate"], "needs_destination": True}
    assert plan_binding_needed(cls, goal, contract) is True
