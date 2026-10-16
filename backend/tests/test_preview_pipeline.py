"""Tests for dynamic agent operation-preview pipelines."""

from agent.plan import ExecutionPlan, PlanStep
from agent.preview_pipeline import build_agent_preview_pipeline
from agent.session import AgentSession


def _session(**kwargs):
    return AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode=kwargs.get("mode", "map"),
        classification_snapshot=kwargs.get("classification_snapshot", {}),
        active_job_id=kwargs.get("active_job_id", "job-1"),
    )


def test_map_only_pipeline():
    plan = ExecutionPlan(
        mode="map",
        steps=[PlanStep(tool_id="map_fields", label="Field mapping")],
        reasoning="architect:map_fields",
    )
    session = _session(
        mode="map",
        classification_snapshot={
            "intent": "map_data",
            "source": "salesforce",
            "source_name": "Production 1",
        },
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert ids == [
        "identify_route",
        "map_fields",
    ]
    assert "connect_source" not in ids


def test_multi_data_ops_pipeline_order_from_plan():
    plan = ExecutionPlan(
        mode="data_ops",
        steps=[
            PlanStep(
                tool_id="run_data_ops_plan",
                arguments={
                    "steps": [
                        {"operation_id": "transform", "prompt": "merge names"},
                        {"operation_id": "filter", "prompt": "drop invalid emails"},
                    ]
                },
            )
        ],
        reasoning="data_ops: transform,filter",
    )
    session = _session(
        mode="data_ops",
        classification_snapshot={"intent": "multi", "operations": ["transform", "filter"]},
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert ids == [
        "data_ops_transform",
        "data_ops_filter",
    ]


def test_filter_data_ops_pipeline():
    plan = ExecutionPlan(
        mode="data_ops",
        steps=[
            PlanStep(
                tool_id="run_data_ops_plan",
                arguments={"steps": [{"operation_id": "filter", "prompt": "remove invalid emails"}]},
            )
        ],
        reasoning="data_ops: filter",
    )
    session = _session(
        mode="data_ops",
        classification_snapshot={"intent": "filter", "operations": ["filter"]},
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert ids == ["data_ops_filter"]
    assert "connect_source" not in ids


def test_multi_source_extract_one_row_per_extract_tool():
    plan = ExecutionPlan(
        mode="extract",
        steps=[
            PlanStep(tool_id="extract_salesforce", label="Extract from SF 1"),
            PlanStep(tool_id="extract_google_sheets", label="Extract data list workbook"),
            PlanStep(
                tool_id="gsheets_execute_operation",
                label="Combine SF 1 + data list workbook",
            ),
            PlanStep(tool_id="map_fields", label="Map fields to contact list"),
            PlanStep(tool_id="start_migration", label="Export to contact list"),
        ],
        reasoning="planner:multi_source",
    )
    session = _session(
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "google_sheets",
            "destination_name": "contact list",
        },
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert ids == [
        "identify_route",
        "extract_salesforce",
        "extract_google_sheets",
        "gsheets_execute_operation",
        "map_fields",
        "start_migration",
    ]
    assert "connect_source" not in ids
    assert pipeline[1]["label"] == "Extract from SF 1"
    assert pipeline[5]["id"] == "start_migration"


def test_unknown_destination_migrate_step_omitted_from_preview():
    plan = ExecutionPlan(
        mode="extract",
        steps=[
            PlanStep(tool_id="extract_salesforce", label="Extract from SF 1"),
            PlanStep(tool_id="start_migration", label="Send data to unknown"),
        ],
        reasoning="planner:extract_export",
    )
    session = _session(
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "unknown",
        },
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert "start_migration" not in ids
    assert ids[-1] == "prepare_preview"


def test_extract_pipeline_includes_setup_phases():
    plan = ExecutionPlan(
        mode="extract",
        steps=[PlanStep(tool_id="extract_salesforce", label="Salesforce extraction")],
        reasoning="architect:extract_salesforce",
    )
    session = _session(
        mode="extract",
        classification_snapshot={"source": "salesforce", "source_name": "Production 1"},
    )
    pipeline = build_agent_preview_pipeline(plan, session)
    ids = [p["id"] for p in pipeline]
    assert ids[:1] == ["identify_route"]
    assert "connect_source" in ids
    assert "prepare_preview" in ids
    assert "map_fields" not in ids
