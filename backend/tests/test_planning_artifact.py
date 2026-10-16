"""Tests for unified planning artifact readiness."""
from __future__ import annotations

from services.planning.plan_extract_gates import (
    PlanningGapKind,
    PlanningReadiness,
    assess_planning_state,
    strip_conversation_plan_from_classification,
)
from services.planning.schemas import Clarification, ExecutionPlan, PlanStep, StepType


def test_readiness_need_clarification_when_plan_has_blocking_gap():
    plan = ExecutionPlan(
        plan_id="p1",
        status="draft",
        clarifications=[
            Clarification(
                id="c1",
                clarification_type="pick_destination_entity",
                prompt="Which object?",
            )
        ],
    )
    artifact = assess_planning_state(
        {"intent": "migrate", "source": "salesforce", "destination": "salesforce"},
        plan,
    )
    assert artifact.readiness == PlanningReadiness.NEED_CLARIFICATION
    assert artifact.gaps[0].kind == PlanningGapKind.DEST_ENTITY


def test_readiness_ready_to_plan_when_steps_awaiting_approval():
    plan = ExecutionPlan(
        plan_id="p2",
        status="awaiting_approval",
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract Contact")],
    )
    artifact = assess_planning_state(
        {
            "intent": "migrate",
            "source": "salesforce",
            "destination": "salesforce",
            "plan_status": "awaiting_approval",
        },
        plan,
    )
    assert artifact.readiness == PlanningReadiness.READY_TO_PLAN
    assert artifact.step_count == 1


def test_readiness_ready_to_execute_when_plan_approved():
    plan = ExecutionPlan(
        plan_id="p3",
        status="approved",
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract Contact")],
    )
    artifact = assess_planning_state(
        {"intent": "migrate", "plan_status": "approved"},
        plan,
    )
    assert artifact.readiness == PlanningReadiness.READY_TO_EXECUTE


def test_readiness_includes_extract_spec_from_migration_intent():
    artifact = assess_planning_state(
        {"intent": "migrate"},
        ExecutionPlan(plan_id="p4", status="draft"),
        migration_intent={
            "driving_entity_key": "Contact",
            "driving_entity_label": "Contact",
            "field_hints": ["FirstName", "Email"],
            "extract_fields_confirmed": True,
        },
        original_prompt="extract contacts from SF1 to SF2",
    )
    assert artifact.extract.entity_key == "Contact"
    assert "FirstName" in artifact.extract.field_hints
    assert artifact.intent.original_prompt == "extract contacts from SF1 to SF2"


def test_assess_planning_state_strips_stale_plan_for_info_intent():
    plan = ExecutionPlan(
        plan_id="p-stale",
        status="awaiting_approval",
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract Contact")],
    )
    classification = {
        "intent": "info",
        "mode": "extract",
        "plan_route": "info_path",
        "plan_status": "awaiting_approval",
        "plan_id": "p-stale",
        "execution_plan": plan.model_dump(mode="json"),
        "source": "salesforce",
        "source_name": "SF 1",
    }
    artifact = assess_planning_state(
        classification,
        plan,
        original_prompt="where do we store customer information in salesforce sf 1 org?",
    )
    assert artifact.readiness == PlanningReadiness.NEED_INTENT
    assert artifact.plan_id is None
    assert artifact.plan_status is None
    assert artifact.step_count == 0
    assert "execution_plan" not in classification
    assert "plan_status" not in classification
    assert classification.get("mode") == "general"
    assert classification.get("plan_route") == "info_path"


def test_strip_conversation_plan_from_classification_noop_for_pipeline():
    classification = {
        "intent": "extract",
        "mode": "extract",
        "plan_status": "draft",
        "execution_plan": {"plan_id": "p1", "status": "draft", "steps": []},
    }
    assert strip_conversation_plan_from_classification(classification) is False
    assert "execution_plan" in classification
