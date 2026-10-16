"""PLAN_SCOPE mechanical caps and binding gates."""
from __future__ import annotations

from services.planning.goal_parser import _apply_plan_scope_llm_data
from services.planning.plan_scope import (
    apply_continuation_scope_cap,
    classification_retains_active_job_at_execution,
    derive_binding_requirements,
    read_plan_scope,
    resolve_pipeline_tokens_from_goal,
    sync_scope_into_classification,
)


def test_read_plan_scope_from_canonical_field():
    cls = {"_plan_scope": {"operations": ["map"]}}
    assert read_plan_scope(cls)["operations"] == ["map"]


def test_read_plan_scope_empty_without_field():
    assert read_plan_scope({}) == {}


def test_apply_plan_scope_strips_data_ops_without_request():
    base = {"operations": ["extract"]}
    out = _apply_plan_scope_llm_data(
        base,
        {
            "pipeline_ops": ["extract"],
            "data_operations": ["filter"],
            "data_ops_requested": False,
        },
    )
    assert out["operations"] == ["extract"]
    assert "data_operations" not in out


def test_apply_plan_scope_keeps_data_ops_when_requested():
    base = {"operations": ["extract"]}
    out = _apply_plan_scope_llm_data(
        base,
        {
            "pipeline_ops": ["extract"],
            "data_operations": ["filter"],
            "data_ops_requested": True,
        },
    )
    assert out["operations"] == ["extract"]
    assert out["data_operations"] == ["filter"]


def test_apply_plan_scope_defaults_data_ops_requested_false():
    base = {"operations": ["extract"]}
    out = _apply_plan_scope_llm_data(
        base,
        {"pipeline_ops": ["extract"], "data_operations": ["filter"]},
    )
    assert out["operations"] == ["extract"]
    assert "data_operations" not in out


def test_apply_plan_scope_continuation_data_ops_override():
    base = {"operations": []}
    out = _apply_plan_scope_llm_data(
        base,
        {"pipeline_ops": [], "data_operations": ["filter"], "data_ops_requested": False},
        data_ops_requested_override=True,
    )
    assert out["data_operations"] == ["filter"]


def test_fresh_extract_only_resolves_single_token():
    cls = {"plan_route": "fresh_path", "intent": "extract", "mode": "extract"}
    goal = {"operations": ["extract"]}
    tokens = resolve_pipeline_tokens_from_goal(cls, goal)
    assert tokens == ["extract"]


def test_apply_continuation_scope_cap_preserves_map_on_data_ops_phase():
    goal = {
        "operations": ["map"],
        "data_operations": ["validate", "filter"],
        "needs_destination": True,
        "destination_phrase": "Test DB",
    }
    capped = apply_continuation_scope_cap(goal, continuation_phase="data_ops", mapping_ready=False)
    assert capped.get("operations") == ["map"]
    assert capped.get("needs_destination") is True
    assert capped.get("destination_phrase") == "Test DB"


def test_apply_continuation_scope_cap_preserves_map_migrate_compound():
    goal = {
        "operations": ["map", "migrate"],
        "data_operations": ["filter"],
        "needs_destination": True,
        "destination_phrase": "Warehouse DB",
    }
    capped = apply_continuation_scope_cap(goal, continuation_phase="data_ops", mapping_ready=False)
    assert capped.get("operations") == ["map", "migrate"]


def test_apply_continuation_scope_cap_ensure_map_for_migrate():
    goal = {"operations": ["migrate"], "needs_destination": True}
    capped = apply_continuation_scope_cap(goal, continuation_phase="migrate", mapping_ready=False)
    assert capped.get("operations") == ["map", "migrate"]


def test_apply_continuation_scope_cap_migrate_only_when_mapping_ready():
    goal = {"operations": ["migrate"]}
    capped = apply_continuation_scope_cap(goal, continuation_phase="migrate", mapping_ready=True)
    assert capped.get("operations") == ["migrate"]


def test_derive_binding_requirements_extract_only():
    req = derive_binding_requirements(["extract"], plan_route="fresh_path")
    assert req == {"source": True, "destination": False, "fields": True}


def test_derive_binding_requirements_continuation_data_ops():
    req = derive_binding_requirements(
        ["data_ops"], plan_route="continuation_path", active_job_id="job-1"
    )
    assert req == {"source": False, "destination": False, "fields": False}


def test_continuation_compound_data_ops_map_resolves_steps():
    cls = {
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "continuation_phase": "data_ops",
    }
    goal = {"operations": ["map"], "data_operations": ["filter"]}
    tokens = resolve_pipeline_tokens_from_goal(cls, goal)
    assert tokens == ["data_ops", "map"]


def test_fresh_compound_extract_filter_map_resolves_steps():
    cls = {"plan_route": "fresh_path", "intent": "extract", "mode": "extract"}
    goal = {
        "operations": ["extract", "map"],
        "data_operations": ["filter"],
        "needs_destination": True,
    }
    tokens = resolve_pipeline_tokens_from_goal(cls, goal)
    assert tokens == ["extract", "data_ops", "map"]


def test_finalize_plan_scope_kind_compound():
    from services.planning.plan_scope import finalize_plan_scope_goal

    g = finalize_plan_scope_goal(
        {"operations": ["map"], "data_operations": ["filter"]}
    )
    assert g["scope_kind"] == "compound"
    assert g["needs_destination"] is True


def test_finalize_injects_create_when_create_destination_without_create_op():
    from services.planning.plan_scope import finalize_plan_scope_goal

    g = finalize_plan_scope_goal(
        {
            "operations": ["extract", "map", "migrate"],
            "create_destination": True,
        }
    )
    assert g["operations"] == ["extract", "create", "map", "migrate"]
    assert g["needs_destination"] is True


def test_sync_scope_into_classification():
    cls = sync_scope_into_classification(
        {"plan_route": "fresh_path"},
        {"operations": ["extract"], "data_operations": ["filter"]},
        ["extract", "data_ops"],
    )
    assert cls["_pipeline_tokens"] == ["extract", "data_ops"]
    assert cls["_binding_requirements"]["destination"] is False
    assert cls["_plan_scope"]["data_operations"] == ["filter"]


def test_classification_retains_job_on_continuation():
    cls = {
        "session_action": "continue_active_job",
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "_continuation_steps": ["map", "migrate"],
    }
    assert classification_retains_active_job_at_execution(cls) is True


def test_classification_clears_job_on_fresh_extract_scope():
    cls = {
        "session_action": "fresh_extract",
        "plan_route": "fresh_path",
        "active_job_id": "stale-job",
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    assert classification_retains_active_job_at_execution(cls) is False


def test_classification_retains_job_for_data_ops_only():
    cls = {
        "plan_route": "continuation_path",
        "active_job_id": "job-1",
        "_continuation_steps": ["data_ops"],
    }
    assert classification_retains_active_job_at_execution(cls) is True
