"""Fresh path: PLAN_SCOPE is authoritative; normalization does not trim ops."""
from __future__ import annotations

from services.planning.connector_guards import classification_needs_destination_endpoint
from services.planning.continuation_policy import apply_fresh_extract_goal_normalization
from services.planning.gap_detector import _destination_connector_type_clarification
from services.planning.resource_catalog import ResourceCatalog


def test_fresh_extract_normalization_preserves_plan_scope_ops():
    """PLAN_SCOPE ops sync to classification — not trimmed by fresh_extract normalization."""
    cls = {
        "session_action": "fresh_extract",
        "plan_route": "fresh_path",
        "intent": "extract",
        "mode": "extract",
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "unknown",
        "operations": ["extract", "map", "migrate"],
    }
    goal = {
        "operations": ["extract", "map", "migrate"],
        "intent": "extract",
        "mode": "extract",
    }
    out, parsed = apply_fresh_extract_goal_normalization(
        cls,
        goal,
        "can you exarct contact data from SF 2 salesforce org",
    )
    assert out["operations"] == ["extract", "map", "migrate"]
    assert parsed["operations"] == ["extract", "map", "migrate"]


def test_fresh_full_pipeline_not_trimmed():
    cls = {
        "session_action": "fresh_full_pipeline",
        "plan_route": "fresh_path",
        "intent": "extract",
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "operations": ["extract", "map", "migrate"],
    }
    goal = {"operations": ["extract", "map", "migrate"]}
    out, parsed = apply_fresh_extract_goal_normalization(
        cls,
        goal,
        "extract contacts from SF 1 and migrate to SF 2",
    )
    assert out["operations"] == ["extract", "map", "migrate"]
    assert parsed["operations"] == ["extract", "map", "migrate"]


def test_fresh_extract_filter_map_preserved():
    cls = {
        "session_action": "fresh_extract",
        "plan_route": "fresh_path",
        "intent": "extract",
        "mode": "extract",
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "unknown",
    }
    goal = {
        "operations": ["extract", "map"],
        "data_operations": ["filter"],
        "scope_kind": "compound",
        "needs_destination": True,
        "destination_phrase": "SF 1",
    }
    out, parsed = apply_fresh_extract_goal_normalization(
        cls,
        goal,
        "extract contact data from SF 2, remove invalid emails, map to SF 1",
    )
    assert parsed["operations"] == ["extract", "map"]
    assert parsed["data_operations"] == ["filter"]


def test_create_scope_not_trimmed():
    cls = {
        "session_action": "no_prior_job",
        "plan_route": "fresh_path",
        "intent": "extract",
        "destination": "unknown",
    }
    goal = {"operations": ["create"], "create_destination": True}
    _, parsed = apply_fresh_extract_goal_normalization(cls, goal, "create google sheet")
    assert parsed["operations"] == ["create"]


def test_fresh_extract_strips_create_when_existing_workbook_pinned():
    cls = {
        "session_action": "fresh_extract",
        "plan_route": "fresh_path",
        "destination": "google_sheets",
        "destination_name": "Contact Information",
        "destination_spreadsheet_id": "abc123",
        "destination_sheet_name": "Sheet1",
        "planner_create_spreadsheet": True,
        "operations": ["extract", "create", "map", "migrate"],
    }
    goal = {"operations": ["extract", "create", "map", "migrate"], "create_destination": True}
    out, parsed = apply_fresh_extract_goal_normalization(cls, goal, "migrate to Contact Information")
    assert "create" not in (parsed.get("operations") or [])
    assert out.get("planner_create_spreadsheet") is False
    assert out.get("mode") == "migrate"


def test_guard_fresh_extract_even_with_stale_ops():
    cls = {
        "session_action": "fresh_extract",
        "intent": "extract",
        "mode": "extract",
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "unknown",
        "operations": ["extract"],
    }
    assert not classification_needs_destination_endpoint(cls)
    assert _destination_connector_type_clarification(cls, ResourceCatalog()) is None


def test_gap_rules_skip_destination_for_fresh_extract():
    from services.intent.gaps import collect_gaps
    from services.intent.state import IntentState
    from services.intent.types import DestinationNeed

    state = IntentState(
        needs_destination=DestinationNeed.NONE.value,
        operations=["extract"],
        destination=IntentState().destination.model_copy(
            update={"connector_type": "unknown", "resolved": False}
        ),
    )
    cls = {
        "session_action": "fresh_extract",
        "operations": ["extract"],
        "intent": "extract",
        "mode": "extract",
        "destination": "unknown",
    }
    gaps = collect_gaps(state, cls)
    assert not any(g["clarification_type"] == "pick_destination_connector" for g in gaps)
