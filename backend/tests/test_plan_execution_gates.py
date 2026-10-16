"""Tests for planner v2 runtime step gates and execution state."""
from __future__ import annotations

from agent.plan import PlanStep
from agent.session import AgentSession
from services.planning.plan_execution_gates import (
    CANONICAL_CREATE_STEP_ID,
    CANONICAL_DATA_OPS_STEP_ID,
    CANONICAL_EXTRACT_STEP_ID,
    CANONICAL_MAP_STEP_ID,
    PlanExecutionState,
    bootstrap_execution_state,
    mark_plan_step_complete,
    merge_execution_state_into_classification,
    persist_execution_state,
    plan_step_blocked_reason,
    record_step_completion,
    record_step_skipped,
    resolve_step_id,
    snapshot_classification_for_pause,
)
from services.planning.plan_runner import agent_plan_from_execution_plan


def _session(**kwargs) -> AgentSession:
    return AgentSession(user_id="u1", conversation_id="c1", mode="extract", **kwargs)


def test_resolve_step_id_defaults_for_extract():
    assert resolve_step_id("extract_salesforce", {}) == CANONICAL_EXTRACT_STEP_ID
    assert resolve_step_id("extract_salesforce", {"step_id": "custom"}) == "custom"


def test_map_blocked_until_extract_complete():
    state = PlanExecutionState()
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["extract"], "step_id": "map"},
        state=state,
        session=_session(),
    )
    assert reason is not None
    assert "extract" in reason.lower()


def test_map_allowed_after_extract_recorded():
    state = PlanExecutionState(completed_step_ids={"extract"}, extraction_complete=True)
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["extract"], "step_id": "map"},
        state=state,
        session=_session(active_job_id="job-1"),
    )
    assert reason is None


def test_create_allowed_when_extract_job_finished_without_step_id_recorded():
    """Regression: extract job done but completed_step_ids empty — bootstrap + job facts unblock create."""
    session = _session(
        active_job_id="job-1",
        classification_snapshot={"plan_status": "approved"},
    )
    state = bootstrap_execution_state(
        session,
        plan_steps=[
            PlanStep(tool_id="extract_salesforce", arguments={}),
            PlanStep(
                tool_id="google_sheets_create_from_prompt",
                arguments={"depends_on": ["extract"], "use_extraction_headers": True},
            ),
        ],
    )
    reason = plan_step_blocked_reason(
        "google_sheets_create_from_prompt",
        {
            "depends_on": ["extract"],
            "use_extraction_headers": True,
            "step_id": "create_sheet",
        },
        state=state,
        session=session,
        label="Create spreadsheet Archian Contacts",
    )
    assert reason is None


def test_record_step_completion_marks_extract_on_job_done_without_step_id():
    state = PlanExecutionState()
    record_step_completion(
        "extract_salesforce",
        {},
        state=state,
        meta={"_extraction_complete": True, "job_id": "job-9"},
    )
    assert state.extraction_complete is True
    assert CANONICAL_EXTRACT_STEP_ID in state.completed_step_ids


def test_create_blocked_when_headers_needed_but_no_job():
    state = PlanExecutionState()
    reason = plan_step_blocked_reason(
        "google_sheets_create_from_prompt",
        {"depends_on": ["extract"], "use_extraction_headers": True, "step_id": "create_sheet"},
        state=state,
        session=_session(),
    )
    assert reason is not None
    assert "extract" in reason.lower()


def test_mark_plan_step_complete_backward_compat():
    step = PlanStep(tool_id="extract_salesforce", arguments={"step_id": "extract"})
    completed: set[str] = set()
    done = mark_plan_step_complete(
        step,
        completed_step_ids=completed,
        extraction_complete=False,
        meta={"_extraction_complete": True, "job_id": "job-9"},
    )
    assert done is True
    assert "extract" in completed


def test_persist_execution_state_writes_snapshot():
    session = _session(classification_snapshot={"plan_id": "p1"})
    session.active_job_id = "job-42"
    state = PlanExecutionState(
        completed_step_ids={"extract", "create_sheet"},
        extraction_complete=True,
    )
    persist_execution_state(session, state)
    assert session.classification_snapshot["active_job_id"] == "job-42"
    assert session.classification_snapshot["_plan_completed_step_ids"] == ["create_sheet", "extract"]


def test_agent_plan_infers_step_id_from_tool():
    session = _session(
        classification_snapshot={
            "source": "salesforce",
            "destination": "google_sheets",
            "plan_status": "approved",
        }
    )
    raw_plan = {
        "steps": [
            {
                "step_type": "extract",
                "label": "Extract from SF 1",
                "depends_on": [],
                "parameters": {
                    "tool_id": "extract_salesforce",
                    "source_name": "SF 1",
                },
            },
            {
                "step_type": "create",
                "label": "Create spreadsheet Archian Contacts",
                "depends_on": ["extract"],
                "parameters": {
                    "tool_id": "google_sheets_create_from_prompt",
                    "spreadsheet_title": "Archian Contacts",
                    "use_extraction_headers": True,
                },
            },
        ],
        "metadata": {"template": "extract_export_create_sheet"},
    }
    agent_plan = agent_plan_from_execution_plan(session, raw_plan)
    assert len(agent_plan.steps) == 2
    assert agent_plan.steps[0].arguments.get("step_id") == CANONICAL_EXTRACT_STEP_ID
    assert agent_plan.steps[1].arguments.get("depends_on") == ["extract"]
    assert agent_plan.steps[1].arguments.get("step_id") == "create_sheet"

    state = PlanExecutionState()
    record_step_completion(
        agent_plan.steps[0].tool_id,
        agent_plan.steps[0].arguments,
        state=state,
        meta={"_extraction_complete": True, "job_id": "job-1"},
    )
    reason = plan_step_blocked_reason(
        agent_plan.steps[1].tool_id,
        agent_plan.steps[1].arguments,
        state=state,
        session=_session(active_job_id="job-1"),
        label=agent_plan.steps[1].label,
    )
    assert reason is None


def test_skipped_create_marks_create_sheet_complete():
    state = PlanExecutionState(completed_step_ids={"extract"}, extraction_complete=True)
    record_step_skipped(
        "google_sheets_create_from_prompt",
        {"step_id": CANONICAL_CREATE_STEP_ID},
        state=state,
    )
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["create_sheet"], "step_id": "map"},
        state=state,
        session=_session(active_job_id="job-1"),
    )
    assert reason is None


def test_bootstrap_infers_create_sheet_from_destination_spreadsheet_id():
    session = _session(
        classification_snapshot={
            "destination_spreadsheet_id": "sheet-abc",
            "plan_status": "approved",
        }
    )
    plan_steps = [
        PlanStep(
            tool_id="google_sheets_create_from_prompt",
            arguments={"depends_on": ["extract"], "step_id": CANONICAL_CREATE_STEP_ID},
        ),
        PlanStep(tool_id="map_fields", arguments={"depends_on": ["create_sheet"], "step_id": "map"}),
    ]
    state = bootstrap_execution_state(session, plan_steps=plan_steps)
    assert CANONICAL_CREATE_STEP_ID in state.completed_step_ids


def test_map_allowed_when_create_finished_via_spreadsheet_id_not_recorded():
    """Regression: create succeeded (spreadsheet_id in snap) but create_sheet not in completed set."""
    session = _session(
        active_job_id="job-1",
        classification_snapshot={
            "destination_spreadsheet_id": "1ygTnVj0yD1mAYOczUfksAmV55D-o_0ImOPguhmFgbyA",
            "active_job_id": "job-1",
            "plan_status": "approved",
        },
    )
    plan_steps = [
        PlanStep(tool_id="extract_salesforce", arguments={"step_id": "extract"}),
        PlanStep(
            tool_id="google_sheets_create_from_prompt",
            arguments={"step_id": CANONICAL_CREATE_STEP_ID, "depends_on": ["extract"]},
        ),
        PlanStep(
            tool_id="map_fields",
            arguments={"depends_on": ["create_sheet"], "step_id": "map"},
            label="Map fields to Archian Contacts",
        ),
    ]
    state = PlanExecutionState(completed_step_ids={"extract"}, extraction_complete=True)
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["create_sheet"], "step_id": "map"},
        state=state,
        session=session,
        label="Map fields to Archian Contacts",
        plan_steps=plan_steps,
    )
    assert reason is None


def test_map_blocked_until_data_ops_complete():
    state = PlanExecutionState(completed_step_ids={"extract"}, extraction_complete=True)
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["data_ops"], "step_id": "map"},
        state=state,
        session=_session(active_job_id="job-1"),
        label="Map fields to Sheet1 in Archian Data",
    )
    assert reason is not None
    assert "data_ops" in reason


def test_map_allowed_when_data_ops_finished_via_operations_applied_not_recorded():
    """Regression: post-extract ran but data_ops missing from completed_step_ids."""
    session = _session(
        active_job_id="job-2",
        active_version=2,
        classification_snapshot={
            "active_job_id": "job-2",
            "extraction_version": 2,
            "operations_applied": ["filter"],
            "plan_status": "approved",
        },
    )
    session.last_result = {
        "job_id": "job-2",
        "extraction_version": 2,
        "operations_applied": ["filter"],
    }
    plan_steps = [
        PlanStep(tool_id="extract_salesforce", arguments={"step_id": "extract"}),
        PlanStep(
            tool_id="run_data_ops_plan",
            arguments={"step_id": CANONICAL_DATA_OPS_STEP_ID, "depends_on": ["extract"]},
        ),
        PlanStep(
            tool_id="map_fields",
            arguments={"depends_on": ["data_ops"], "step_id": "map"},
            label="Map fields to Sheet1 in Archian Data",
        ),
    ]
    state = PlanExecutionState(completed_step_ids={"extract"}, extraction_complete=True)
    reason = plan_step_blocked_reason(
        "map_fields",
        {"depends_on": ["data_ops"], "step_id": "map"},
        state=state,
        session=session,
        label="Map fields to Sheet1 in Archian Data",
        plan_steps=plan_steps,
    )
    assert reason is None


def test_bootstrap_infers_data_ops_from_operations_applied():
    session = _session(
        active_job_id="job-2",
        classification_snapshot={
            "operations_applied": ["filter"],
            "extraction_version": 2,
            "active_job_id": "job-2",
            "plan_status": "approved",
        },
    )
    plan_steps = [
        PlanStep(
            tool_id="run_data_ops_plan",
            arguments={"step_id": CANONICAL_DATA_OPS_STEP_ID},
        ),
    ]
    state = bootstrap_execution_state(session, plan_steps=plan_steps)
    assert CANONICAL_DATA_OPS_STEP_ID in state.completed_step_ids


def test_record_step_completion_marks_data_ops_on_operations_applied():
    state = PlanExecutionState()
    record_step_completion(
        "run_data_ops_plan",
        {"step_id": CANONICAL_DATA_OPS_STEP_ID},
        state=state,
        meta={
            "job_id": "job-2",
            "extraction_version": 2,
            "operations_applied": ["filter"],
        },
    )
    assert CANONICAL_DATA_OPS_STEP_ID in state.completed_step_ids


def test_full_extract_data_ops_map_plan_steps_unblocked_sequentially():
    """Simulate SF extract + filter data_ops + map — artifacts unblock map."""
    session = _session(
        classification_snapshot={
            "source": "salesforce",
            "destination": "google_sheets",
            "plan_status": "approved",
        }
    )
    raw_plan = {
        "steps": [
            {"step_type": "extract", "parameters": {"tool_id": "extract_salesforce"}},
            {
                "step_type": "transform",
                "depends_on": ["extract"],
                "parameters": {
                    "tool_id": "run_data_ops_plan",
                    "steps": [{"operation_id": "filter", "prompt": "remove invalid emails"}],
                },
            },
            {
                "step_type": "map",
                "depends_on": ["data_ops"],
                "parameters": {"tool_id": "map_fields"},
            },
        ],
        "metadata": {"template": "extract_map_migrate"},
    }
    agent_plan = agent_plan_from_execution_plan(session, raw_plan)
    state = PlanExecutionState()

    record_step_completion(
        agent_plan.steps[0].tool_id,
        agent_plan.steps[0].arguments,
        state=state,
        meta={"_extraction_complete": True, "job_id": "job-1"},
    )
    session.active_job_id = "job-1"
    session.classification_snapshot["active_job_id"] = "job-1"

    assert (
        plan_step_blocked_reason(
            agent_plan.steps[1].tool_id,
            agent_plan.steps[1].arguments,
            state=state,
            session=session,
            plan_steps=agent_plan.steps,
        )
        is None
    )

    session.active_job_id = "job-2"
    session.active_version = 2
    session.last_result = {
        "job_id": "job-2",
        "extraction_version": 2,
        "operations_applied": ["filter"],
    }
    session.classification_snapshot.update(
        {
            "active_job_id": "job-2",
            "operations_applied": ["filter"],
            "extraction_version": 2,
        }
    )

    assert (
        plan_step_blocked_reason(
            agent_plan.steps[2].tool_id,
            agent_plan.steps[2].arguments,
            state=state,
            session=session,
            label=agent_plan.steps[2].label,
            plan_steps=agent_plan.steps,
        )
        is None
    )


def test_full_extract_create_map_plan_steps_unblocked_sequentially():
    """Simulate approved 4-step plan: each step unblocks the next via artifacts + recording."""
    session = _session(
        classification_snapshot={
            "source": "salesforce",
            "destination": "google_sheets",
            "plan_status": "approved",
        }
    )
    raw_plan = {
        "steps": [
            {
                "step_type": "extract",
                "parameters": {"tool_id": "extract_salesforce"},
            },
            {
                "step_type": "create",
                "depends_on": ["extract"],
                "parameters": {
                    "tool_id": "google_sheets_create_from_prompt",
                    "use_extraction_headers": True,
                },
            },
            {
                "step_type": "map",
                "depends_on": ["create_sheet"],
                "parameters": {"tool_id": "map_fields"},
            },
        ],
        "metadata": {"template": "extract_export_create_sheet"},
    }
    agent_plan = agent_plan_from_execution_plan(session, raw_plan)
    state = PlanExecutionState()

    record_step_completion(
        agent_plan.steps[0].tool_id,
        agent_plan.steps[0].arguments,
        state=state,
        meta={"_extraction_complete": True, "job_id": "job-1"},
    )
    session.active_job_id = "job-1"
    session.classification_snapshot["active_job_id"] = "job-1"

    assert (
        plan_step_blocked_reason(
            agent_plan.steps[1].tool_id,
            agent_plan.steps[1].arguments,
            state=state,
            session=session,
            plan_steps=agent_plan.steps,
        )
        is None
    )

    session.classification_snapshot["destination_spreadsheet_id"] = "sheet-new"
    assert (
        plan_step_blocked_reason(
            agent_plan.steps[2].tool_id,
            agent_plan.steps[2].arguments,
            state=state,
            session=session,
            label=agent_plan.steps[2].label,
            plan_steps=agent_plan.steps,
        )
        is None
    )


def test_migrate_allowed_when_map_finished_via_mapping_config_not_recorded():
    """Regression: map UI ready but 'map' missing from completed_step_ids — artifacts unblock migrate."""
    session = _session(
        active_job_id="job-1",
        classification_snapshot={
            "destination_spreadsheet_id": "sheet-1",
            "active_job_id": "job-1",
            "plan_status": "approved",
        },
    )
    session.mapping_config = {"Email": {"source": "Email", "destination": "Email"}}
    session.last_result = {
        "action": "map_fields_ready",
        "mapping_id": "mapping-123",
        "mapping_config": session.mapping_config,
        "job_id": "job-1",
    }
    plan_steps = [
        PlanStep(tool_id="extract_salesforce", arguments={"step_id": "extract"}),
        PlanStep(
            tool_id="google_sheets_create_from_prompt",
            arguments={"step_id": CANONICAL_CREATE_STEP_ID},
        ),
        PlanStep(tool_id="map_fields", arguments={"step_id": CANONICAL_MAP_STEP_ID}),
        PlanStep(
            tool_id="start_migration",
            arguments={"depends_on": ["map"], "step_id": "migrate"},
            label="Migrate to llm Contacts",
        ),
    ]
    state = PlanExecutionState(
        completed_step_ids={"extract", CANONICAL_CREATE_STEP_ID},
        extraction_complete=True,
    )
    reason = plan_step_blocked_reason(
        "start_migration",
        {"depends_on": ["map"], "step_id": "migrate"},
        state=state,
        session=session,
        label="Migrate to llm Contacts",
        plan_steps=plan_steps,
    )
    assert reason is None


def test_record_step_completion_marks_map_on_map_fields_ready():
    state = PlanExecutionState()
    record_step_completion(
        "map_fields",
        {"step_id": CANONICAL_MAP_STEP_ID},
        state=state,
        meta={
            "action": "map_fields_ready",
            "mapping_id": "mapping-99",
            "mapping_config": {"Email": {}},
        },
    )
    assert CANONICAL_MAP_STEP_ID in state.completed_step_ids


def test_full_four_step_plan_migrate_unblocked_after_map_artifacts():
    session = _session(
        classification_snapshot={
            "destination_spreadsheet_id": "sheet-1",
            "active_job_id": "job-1",
            "plan_status": "approved",
        },
        active_job_id="job-1",
    )
    plan_steps = [
        PlanStep(tool_id="extract_salesforce", arguments={"step_id": "extract"}),
        PlanStep(
            tool_id="google_sheets_create_from_prompt",
            arguments={"step_id": CANONICAL_CREATE_STEP_ID},
        ),
        PlanStep(
            tool_id="map_fields",
            arguments={"depends_on": ["create_sheet"], "step_id": CANONICAL_MAP_STEP_ID},
        ),
        PlanStep(
            tool_id="start_migration",
            arguments={"depends_on": ["map"], "step_id": "migrate"},
        ),
    ]
    state = PlanExecutionState(completed_step_ids={"extract", CANONICAL_CREATE_STEP_ID})
    session.mapping_config = {"Phone": {"source": "Phone", "destination": "Phone"}}
    session.last_result = {
        "action": "map_fields_ready",
        "mapping_config": session.mapping_config,
        "job_id": "job-1",
    }
    reason = plan_step_blocked_reason(
        "start_migration",
        {"depends_on": ["map"], "step_id": "migrate"},
        state=state,
        session=session,
        plan_steps=plan_steps,
    )
    assert reason is None


def test_snapshot_classification_for_pause_persists_completed_ids():
    session = _session(classification_snapshot={"plan_id": "p1"})
    session.active_job_id = "job-7"
    state = PlanExecutionState(
        completed_step_ids={"extract"},
        extraction_complete=True,
    )
    pause_cls = snapshot_classification_for_pause(session, state)
    assert pause_cls["_plan_completed_step_ids"] == ["extract"]
    assert pause_cls["active_job_id"] == "job-7"


def test_merge_execution_state_from_history():
    history = [
        {
            "metadata": {
                "event_type": "planner_plan_execution_clarification",
                "planner_pending": {
                    "classification": {
                        "_plan_completed_step_ids": ["extract"],
                        "plan_resume_step_index": 0,
                        "active_job_id": "job-55",
                    }
                },
            }
        }
    ]
    out = merge_execution_state_into_classification({}, conversation_history=history)
    assert out["_plan_completed_step_ids"] == ["extract"]
    assert out["active_job_id"] == "job-55"
    assert out["plan_resume_step_index"] == 0
