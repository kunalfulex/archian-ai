"""Golden: SF → existing Google Sheet must not retain stale create flags."""
from __future__ import annotations

from services.architect_agent import _effective_architect_prompt
from services.planning.create_metadata import (
    apply_create_metadata_to_classification,
    should_materialize_create_step,
)
from services.planning.goal_reconciliation import reconcile_existing_destination_flags
from services.planning.plan_approve import approve_execution_plan
from services.planning.plan_scope import read_plan_scope, sync_scope_into_classification
from services.planning.schemas import ExecutionPlan, PlanContract, PlanEntityRef, PlanStep, StepType
from services.planning.scope_validator import validate_plan_scope_goal


def _contact_information_classification():
    return {
        "plan_route": "fresh_path",
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "google_sheets",
        "destination_name": "Contact Information",
        "destination_spreadsheet_id": "1lKFb9ZSDNZbWr_QvwjkNJGz8Wrn_xh4tKfEnu_VmtsM",
        "destination_sheet_name": "Sheet1",
        "planner_create_spreadsheet": True,
        "operations": ["extract", "create", "map", "migrate"],
    }


def _contact_information_goal():
    return {
        "operations": ["extract", "create", "map", "migrate"],
        "create_destination": True,
    }


def _contact_information_contract():
    return PlanContract(
        destination=PlanEntityRef(
            connector_type="google_sheets",
            display_name="Contact Information",
            entity_key="1lKFb9ZSDNZbWr_QvwjkNJGz8Wrn_xh4tKfEnu_VmtsM::Sheet1",
            spreadsheet_id="1lKFb9ZSDNZbWr_QvwjkNJGz8Wrn_xh4tKfEnu_VmtsM",
            sheet_name="Sheet1",
            resolution_source="llm_bind",
            confidence=0.9,
        )
    )


def test_scope_validator_strips_create_when_dest_pinned():
    cls = _contact_information_classification()
    goal = validate_plan_scope_goal(_contact_information_goal(), cls)
    assert "create" not in (goal.get("operations") or [])
    assert goal.get("create_destination") is False


def test_apply_create_metadata_noop_when_existing_workbook_pinned():
    cls = _contact_information_classification()
    goal = _contact_information_goal()
    out = apply_create_metadata_to_classification(cls, goal)
    assert out.get("planner_create_spreadsheet") is False
    assert "create" not in (out.get("operations") or [])


def test_reconcile_clears_mode_create_for_existing_sheet():
    cls = _contact_information_classification()
    cls["planner_workbook_picker_done"] = True
    cls["_destination_endpoint_clarified"] = True
    goal = _contact_information_goal()
    contract = _contact_information_contract()
    out, parsed, changed = reconcile_existing_destination_flags(cls, goal, contract)
    assert changed is True
    assert out.get("mode") == "migrate"
    assert out.get("intent") == "migrate_data"
    assert out.get("planner_create_spreadsheet") is False
    assert should_materialize_create_step(out, parsed) is False


def test_effective_architect_prompt_no_create_suffix_for_existing_sheet():
    cls = _contact_information_classification()
    goal = _contact_information_goal()
    out, parsed, _ = reconcile_existing_destination_flags(
        cls, goal, _contact_information_contract()
    )
    prompt = _effective_architect_prompt(
        "can you build customer pipeline between SF 1 and Contact Information google sheet",
        out,
    )
    assert "Create a new Google spreadsheet" not in prompt
    assert should_materialize_create_step(out, parsed) is False


def test_plan_approve_clears_stale_create_flags():
    cls = _contact_information_classification()
    cls["planner_workbook_picker_done"] = True
    cls["_destination_endpoint_clarified"] = True
    goal = _contact_information_goal()
    out, parsed, _ = reconcile_existing_destination_flags(
        cls, goal, _contact_information_contract()
    )
    plan = ExecutionPlan(
        plan_id="plan-1",
        status="awaiting_approval",
        steps=[
            PlanStep(
                step_type=StepType.EXTRACT,
                label="Extract",
                parameters={"tool_id": "extract_salesforce"},
            ),
            PlanStep(
                step_type=StepType.MAP,
                label="Map",
                parameters={"tool_id": "map_fields"},
            ),
            PlanStep(
                step_type=StepType.MIGRATE,
                label="Migrate",
                parameters={"tool_id": "start_migration"},
            ),
        ],
        metadata={
            "plan_contract": _contact_information_contract().model_dump(mode="json"),
            "goal": parsed,
        },
    )
    out = sync_scope_into_classification(out, parsed, ["extract", "map", "migrate"])
    pending = {
        "classification": {**out, "execution_plan": plan.model_dump(mode="json"), "planner_create_spreadsheet": True},
        "execution_plan": plan.model_dump(mode="json"),
        "original_prompt": "can you build customer pipeline between SF 1 and Contact Information google sheet",
    }
    approved = approve_execution_plan(pending)
    assert approved.get("mode") == "migrate"
    assert approved.get("planner_create_spreadsheet") is False
    assert "create" not in (read_plan_scope(approved) or {}).get("operations", [])
