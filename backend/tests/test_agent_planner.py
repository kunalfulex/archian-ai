"""Tests for data_ops step ordering in agent.planner."""

import asyncio

from agent.planner import (
    Planner,
    _canonicalize_operations_list,
    _parse_step_order_json,
)
from agent.session import infer_agent_mode_from_classification


def test_canonicalize_preserves_classifier_order():
    assert _canonicalize_operations_list(["filter", "transform"]) == ["filter", "transform"]
    assert _canonicalize_operations_list(["Validate", "dedupe"]) == ["validate", "dedupe"]


def test_canonicalize_dedupes_and_maps_aliases():
    raw = ["filter", "transformation", "filter", "multi", "unknown"]
    assert _canonicalize_operations_list(raw) == ["filter", "transform"]


def test_parse_step_order_json_valid():
    allowed = ["transform", "filter"]
    got = _parse_step_order_json('{"order": ["filter", "transform"]}', allowed)
    assert got == ["filter", "transform"]


def test_parse_step_order_json_rejects_bad():
    allowed = ["transform", "filter"]
    assert _parse_step_order_json('{"order": ["filter"]}', allowed) is None


def test_infer_agent_mode_from_classification_maps_data_ops_intents():
    assert infer_agent_mode_from_classification({"intent": "transform"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "filter"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "dedupe"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "validate"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "validation"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "cleanup"}) == "data_ops"
    assert infer_agent_mode_from_classification({"intent": "multi"}) == "data_ops"


def test_infer_agent_mode_from_classification_prefers_explicit_mode():
    assert infer_agent_mode_from_classification({"mode": "general", "intent": "transform"}) == "general"
    assert infer_agent_mode_from_classification({"mode": "data_ops", "intent": "general"}) == "data_ops"


def test_build_plan_data_ops_emits_run_data_ops_step():
    """Ad-hoc data_ops turns run fixed run_data_ops_plan via AgentRuntime."""
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="data_ops",
        active_job_id="job1",
        operations=["filter", "transform"],
    )
    plan = asyncio.run(Planner().build_plan("anything", session))
    assert len(plan.steps) == 1
    assert plan.steps[0].tool_id == "run_data_ops_plan"
    assert plan.reasoning == "data_ops:plan_scoped"


def test_pipeline_turn_without_execution_plan_awaits_plan_assembly():
    """Extract/migrate/map turns without approved plan defer to planner v2 assembly."""
    from agent.session import AgentSession

    cases = [
        (
            "extract",
            {
                "source": "salesforce",
                "destination": "salesforce",
                "intent": "extract",
            },
            "pull contacts",
        ),
        (
            "migrate",
            {
                "source": "salesforce",
                "destination": "sql",
                "intent": "migrate",
                "destination_name": "Test DB",
                "source_name": "Production 2",
            },
            "start the migration",
            "job-abc",
        ),
        (
            "migrate",
            {
                "source": "salesforce",
                "destination": "sql",
                "intent": "migrate",
                "destination_name": "Test DB",
            },
            "extract from Production 2 and migrate to Test DB",
            None,
        ),
        (
            "map",
            {
                "source": "google_sheets",
                "destination": "google_sheets",
                "intent": "map_data",
                "source_name": "contact list",
                "destination_name": "contact list",
            },
            "map the data same sheet",
            "job-gsheets-1",
        ),
    ]
    for mode, classification, prompt, *rest in cases:
        active_job_id = rest[0] if rest else None
        session = AgentSession(
            user_id="u",
            conversation_id="c1",
            mode=mode,
            active_job_id=active_job_id,
            classification_snapshot=classification,
            prompt=prompt,
        )
        plan = asyncio.run(Planner().build_plan(prompt, session))
        assert plan.steps == []
        assert plan.reasoning == "plan_scoped:awaiting_plan_execution"


def test_architect_plan_fallback_general():
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="general",
        classification_snapshot={
            "source": "unknown",
            "destination": "unknown",
            "intent": "general",
        },
        prompt="hello",
    )
    plan = asyncio.run(Planner().build_plan("hello", session))
    assert plan.steps[0].tool_id == "general_ai_response"


def test_architect_skips_micro_plan_when_plan_scope_present():
    """Regression: gsheets micro-tools must not override planner v2 goal-driven plans."""
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={
            "source": "google_sheets",
            "destination": "google_sheets",
            "intent": "extract",
            "_plan_scope": {"operations": ["extract"], "intent": "extract"},
        },
        prompt="pull from Leads tab",
    )
    plan = asyncio.run(Planner().build_plan("pull from Leads tab", session))
    assert plan.reasoning == "plan_scoped:awaiting_plan_execution"
    assert plan.steps == []


def test_approved_execution_plan_not_overridden_by_general_mode():
    """Regression: approved execution plan must run even if session mode inferred as general."""
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        classification_snapshot={
            "source": "google_sheets",
            "destination": "salesforce",
            "intent": "join",
            "mode": "join",
            "plan_status": "approved",
            "execution_plan": {
                "status": "approved",
                "steps": [
                    {
                        "step_type": "extract",
                        "label": "Extract from Service Appointments",
                        "parameters": {
                            "tool_id": "extract_google_sheets",
                            "source_name": "Service Appointments",
                            "destination": "salesforce",
                        },
                    },
                    {
                        "tool_id": "run_data_ops_plan",
                        "step_type": "transform",
                        "label": "Transform columns",
                        "parameters": {
                            "steps": [
                                {
                                    "operation_id": "transform",
                                    "prompt": "combine columns",
                                }
                            ]
                        },
                    },
                ],
            },
        },
        prompt="Extract and combine columns",
    )
    plan = asyncio.run(Planner().build_plan("Extract and combine columns", session))
    assert len(plan.steps) == 2
    assert plan.steps[0].tool_id != "general_ai_response"
    assert [s.tool_id for s in plan.steps] == ["extract_google_sheets", "run_data_ops_plan"]
