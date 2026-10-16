"""PLAN_SCOPE authority — sole scope and needs_* flags."""
from __future__ import annotations

from services.planning.connector_guards import classification_needs_destination_endpoint
from services.planning.goal_parser import parse_goal, _apply_plan_scope_llm_data
from services.planning.plan_scope import finalize_plan_scope_goal


def test_parse_goal_scope_base_only_empty_operations():
    cls = {
        "intent": "migrate_data",
        "operations": ["extract", "map", "migrate"],
    }
    goal = parse_goal(cls, "extract contact from SF 1", scope_base_only=True)
    assert goal.get("operations") == []


def test_fresh_extract_scope_does_not_require_destination():
    cls = {
        "session_action": "fresh_extract",
        "plan_route": "fresh_path",
        "_plan_scope": {"operations": ["extract"], "needs_destination": False},
        "_pipeline_tokens": ["extract"],
        "operations": ["extract"],
        "intent": "extract",
        "mode": "extract",
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "unknown",
    }
    assert not classification_needs_destination_endpoint(cls)


def test_apply_plan_scope_needs_source_and_extract_sources():
    base = {"operations": ["extract"]}
    data = {
        "pipeline_ops": ["extract", "join"],
        "needs_source": True,
        "join_key": "Email",
        "extract_sources": [
            {"connector_type": "google_sheets", "display_name": "Book A", "sheet_name": "Tab1"},
            {"connector_type": "google_sheets", "display_name": "Book A", "sheet_name": "Tab2"},
        ],
    }
    out = _apply_plan_scope_llm_data(base, data)
    assert out.get("needs_source") is True
    assert len(out.get("extract_sources") or []) == 2
    assert out.get("multi_source_extract") is True


def test_finalize_plan_scope_derives_needs_source_for_extract():
    goal = finalize_plan_scope_goal({"operations": ["extract"]})
    assert goal.get("needs_source") is True
