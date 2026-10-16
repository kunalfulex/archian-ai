"""Conversational / general turns must not trigger post-extract or stale data_ops."""

from services.planning.agent_runtime_enrichment import enrich_classification_for_agent_runtime
from services.planning.intent_routing import (
    classification_is_general_turn,
    should_route_to_general_response,
)


def test_hi_after_failed_extract_not_post_extract():
    """Regression: stale operations[] + extraction_completed must not route Hi to data_ops."""
    state = {"state": "extraction_completed", "active_job_id": "job-failed"}
    c = {
        "intent": "info",
        "mode": None,
        "operations": ["transform"],
        "source": "google_sheets",
        "source_name": "contact list",
        "active_job_id": "job-failed",
        "plan_route": "general_path",
    }
    out = enrich_classification_for_agent_runtime(c, state, prompt="Hi")
    assert out.get("mode") == "general"
    assert should_route_to_general_response(out, "Hi")


def test_stale_transform_ops_requires_gate0_for_hi():
    """Hi must not keyword-override pipeline intent; Gate 0 sets plan_route."""
    c = {"intent": "transform", "mode": "data_ops", "operations": ["transform"]}
    assert not classification_is_general_turn(c, "Hi")
    c_gate0 = {
        **c,
        "plan_route": "general_path",
        "conversational_turn": True,
        "intent": "general",
    }
    assert classification_is_general_turn(c_gate0, "Hi")


def test_transform_with_explicit_prompt_uses_planner_not_bypass():
    c = {"intent": "transform", "mode": "data_ops", "operations": ["transform"]}
    prompt = "remove duplicate rows from the extracted data"
    assert not classification_is_general_turn(c, prompt)


def test_approved_data_ops_plan_not_general_turn():
    """Approved planner plan must execute via plan runner, not ad-hoc post_extract stream."""
    c = {
        "intent": "dedupe",
        "mode": "data_ops",
        "operations": ["dedupe"],
        "plan_status": "approved",
        "execution_plan": {
            "status": "approved",
            "steps": [{"step_type": "transform", "parameters": {"tool_id": "run_data_ops_plan"}}],
        },
    }
    prompt = "remove duplicate rows based on email"
    assert not classification_is_general_turn(c, prompt)


def test_off_topic_routes_general():
    c = {"intent": "general", "off_topic": True, "plan_route": "general_path"}
    assert should_route_to_general_response(c, "write me a python script for a todo app")
    assert classification_is_general_turn(c, "write me a python script for a todo app")


def test_salesforce_objects_info_query_routes_general():
    """Schema/resource questions must not trigger extract plans (Gate 0 plan_route)."""
    c = {
        "intent": "info",
        "mode": "general",
        "source": "salesforce",
        "source_name": "SF 1",
        "plan_route": "info_path",
    }
    out = enrich_classification_for_agent_runtime(c, None, prompt="What Salesforce objects are available in SF 1?")
    assert out.get("mode") == "general"
    assert should_route_to_general_response(out, "What Salesforce objects are available in SF 1?")


def test_planner_skips_v2_extract_on_hi():
    """Approved extract_only plan must not run on a greeting."""
    import asyncio

    from agent.planner import Planner
    from agent.session import AgentSession

    session = AgentSession(
        user_id="u",
        conversation_id="c1",
        mode="general",
        active_job_id="job-0-records",
        classification_snapshot={
            "intent": "general",
            "mode": "general",
            "conversational_turn": True,
            "plan_status": "approved",
            "execution_plan": {
                "status": "approved",
                "steps": [{"tool_id": "extract_google_sheets", "arguments": {}}],
            },
        },
        prompt="Hi",
    )
    plan = asyncio.run(Planner().build_plan("Hi", session))
    assert plan.mode == "general"
    assert plan.steps[0].tool_id == "general_ai_response"
