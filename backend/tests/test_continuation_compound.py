"""Multi-operation continuation planning (LLM goal + mechanical step resolver)."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from services.intent.continuation_planning import (
    classification_from_job_context,
    plan_from_job_context,
)
from services.intent.job_context import JobContext, ResourcePin
from services.intent.session_continuation import SessionContinuationDecision
from services.planning.continuation_policy import (
    apply_continuation_goal_to_classification,
    continuation_needs_map_connector_resolution,
    resolve_continuation_steps,
)
from services.planning.plan_scope import tokens_need_map_destination_llm
from services.planning.resource_catalog import ResourceCatalog


def _extracted_job(**kwargs) -> JobContext:
    defaults = dict(
        job_id="job-1",
        pipeline_stage="extracted",
        last_completed_step="extract",
        mapping_ready=False,
        source=ResourcePin(
            connector_type="salesforce",
            display_name="SF 2",
            entity_key="Contact",
        ),
    )
    defaults.update(kwargs)
    return JobContext(**defaults)


def _continue_decision(**kwargs) -> SessionContinuationDecision:
    defaults = dict(
        session_action="continue_active_job",
        continuation_phase="data_ops",
        reuse_job_id=True,
        job_id="job-1",
        confidence=0.9,
        decision_source="llm",
    )
    defaults.update(kwargs)
    return SessionContinuationDecision(**defaults)


def test_resolve_continuation_steps_filter_and_migrate():
    job = _extracted_job()
    goal = {
        "operations": ["map", "migrate"],
        "data_operations": ["filter"],
    }
    steps = resolve_continuation_steps(goal, job, continuation_phase="data_ops")
    assert steps == ["data_ops", "map", "migrate"]


def test_resolve_continuation_steps_migrate_only_when_mapped():
    job = _extracted_job(mapping_ready=True, pipeline_stage="mapped", last_completed_step="map")
    goal = {"operations": ["migrate"]}
    steps = resolve_continuation_steps(goal, job, continuation_phase="migrate")
    assert steps == ["migrate"]


def test_resolve_continuation_steps_migrate_only_when_scope_says_so():
    """Migrate in PLAN_SCOPE gets map prepended when mapping is not ready."""
    job = _extracted_job()
    goal = {"operations": ["migrate"]}
    steps = resolve_continuation_steps(goal, job, continuation_phase="migrate")
    assert steps == ["map", "migrate"]


def test_resolve_continuation_steps_map_and_migrate_from_scope():
    job = _extracted_job()
    goal = {"operations": ["map", "migrate"]}
    steps = resolve_continuation_steps(goal, job, continuation_phase="migrate")
    assert steps == ["map", "migrate"]


def test_create_only_does_not_need_map_connector_resolution():
    job = _extracted_job(mapping_ready=True, pipeline_stage="migrated", last_completed_step="migrate")
    steps = ["create"]
    goal = {"operations": ["create"], "needs_destination": True}
    assert not tokens_need_map_destination_llm(steps, job)
    assert not continuation_needs_map_connector_resolution(steps, goal, job)


def test_apply_continuation_goal_strips_destination_for_data_ops_only():
    job = _extracted_job(
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            connector_id="conn-1",
            entity_key="Contact",
        ),
        destination_object_api_name="Contact",
    )
    decision = _continue_decision(continuation_phase="data_ops")
    cls = {"active_job_id": job.job_id, "source": "salesforce", "source_name": "SF 2"}
    goal = {"operations": [], "data_operations": ["filter"]}
    out = apply_continuation_goal_to_classification(cls, goal, job, decision)
    assert out["_continuation_steps"] == ["data_ops"]
    assert out.get("destination_name") is None
    assert out.get("planner_create_spreadsheet") is not True


def test_apply_continuation_goal_create_sets_planner_flag():
    job = _extracted_job(mapping_ready=True, pipeline_stage="migrated", last_completed_step="migrate")
    decision = _continue_decision(continuation_phase="none")
    cls = {"active_job_id": job.job_id, "plan_route": "continuation_path"}
    goal = {"operations": ["create"], "needs_destination": True, "destination_phrase": "Google Sheet"}
    out = apply_continuation_goal_to_classification(cls, goal, job, decision)
    assert out["_continuation_steps"] == ["create"]
    assert out.get("planner_create_spreadsheet") is True


def test_apply_continuation_scope_cap_preserves_data_ops_plus_map():
    """PLAN_SCOPE compound data_ops + map is preserved (no S01 phase strip)."""
    job = _extracted_job()
    decision = _continue_decision(continuation_phase="data_ops")
    cls = {
        "active_job_id": job.job_id,
        "source": "salesforce",
        "source_name": "SF 2",
    }
    goal = {
        "operations": ["map"],
        "data_operations": ["validate", "transform", "filter"],
        "needs_destination": True,
        "destination_phrase": "Test DB",
    }
    out = apply_continuation_goal_to_classification(cls, goal, job, decision)
    assert out["_continuation_steps"] == ["data_ops", "map"]
    assert out.get("destination_name") is None or out.get("needs_destination")


def test_apply_continuation_scope_cap_preserves_plan_scope_ops():
    """Explicit map+migrate compound survives S01 data_ops phase cap."""
    job = _extracted_job()
    decision = _continue_decision(continuation_phase="data_ops")
    cls = {
        "active_job_id": job.job_id,
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    goal = {
        "operations": ["map", "migrate"],
        "data_operations": ["filter", "transform"],
    }
    out = apply_continuation_goal_to_classification(cls, goal, job, decision)
    assert out["_continuation_steps"] == ["data_ops", "map", "migrate"]


def test_apply_continuation_goal_keeps_destination_for_filter_and_migrate():
    job = _extracted_job()
    decision = _continue_decision(continuation_phase="migrate")
    cls = {
        "active_job_id": job.job_id,
        "source": "salesforce",
        "source_name": "SF 2",
        "destination": "salesforce",
        "destination_name": "SF 1",
    }
    goal = {
        "operations": ["map", "migrate"],
        "data_operations": ["filter"],
    }
    out = apply_continuation_goal_to_classification(cls, goal, job, decision)
    assert out["_continuation_steps"] == ["data_ops", "map", "migrate"]
    assert out.get("destination_name") == "SF 1"


def test_plan_from_job_context_compound_three_steps():
    async def _run():
        job = _extracted_job(
            destination=ResourcePin(
                connector_type="salesforce",
                display_name="SF 1",
                entity_key="Contact",
            ),
        )
        decision = _continue_decision(
            continuation_phase="migrate",
            reasoning_short="Filter invalid emails and migrate",
        )
        prompt = "remove invalid email records and migrate the data to SF 1 org"
        mock_goal = {
            "operations": ["map", "migrate"],
            "data_operations": ["filter"],
            "intent": "migrate_data",
        }

        async def _mock_goal(*args, **kwargs):
            return dict(mock_goal)

        async def _passthrough_kernel(session, **kwargs):
            return session

        with patch(
            "services.planning.goal_parser.enrich_continuation_scope_from_llm",
            side_effect=_mock_goal,
        ), patch(
            "services.planning.connector_guards.validate_classification_resources",
            side_effect=lambda c, _cat: c,
        ), patch(
            "services.planning.planning_kernel.run_planning_kernel",
            side_effect=_passthrough_kernel,
        ):
            out = await plan_from_job_context(
                user_id="u1",
                prompt=prompt,
                session_decision=decision,
                job=job,
                catalog=ResourceCatalog(),
            )

        assert out.get("_continuation_steps") == ["data_ops", "map", "migrate"]

    asyncio.run(_run())


def test_classification_from_job_context_with_explicit_goal():
    job = _extracted_job()
    decision = _continue_decision(continuation_phase="data_ops")
    goal = {"operations": [], "data_operations": ["filter"]}
    cls = classification_from_job_context(decision, job, goal=goal)
    assert cls["_continuation_steps"] == ["data_ops"]
    assert cls["operations"] == ["filter"]
