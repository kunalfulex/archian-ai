from services.planning.agent_runtime_enrichment import enrich_classification_for_agent_runtime
from services.planning.goal_parser import resolve_data_operations
from services.planning.plan_builder import build_plan_steps


GOLDEN_PROMPT = (
    "extract data from contact list google sheet and migrate to same sheet, "
    "remove duplicate records and invalid email id records"
)


def test_resolve_data_operations_from_plan_scope():
    goal = {"operations": ["extract", "map", "migrate"], "data_operations": ["dedupe", "filter"]}
    ops = resolve_data_operations({}, GOLDEN_PROMPT, goal)
    assert ops == ["dedupe", "filter"]


def test_resolve_data_operations_from_plan_scope_classification():
    cls = {
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "data_operations": ["dedupe", "filter"],
        }
    }
    ops = resolve_data_operations(cls, GOLDEN_PROMPT)
    assert ops == ["dedupe", "filter"]


def test_compound_extract_map_migrate_includes_data_ops_step():
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "source_name": "contact list",
        "destination_name": "contact list",
        "intent": "extract",
        "mode": "extract",
        "destination_same_as_source": True,
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "data_operations": ["dedupe", "filter"],
        },
    }
    plan = build_plan_steps(classification, prompt=GOLDEN_PROMPT)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → data_ops → map → migrate"
    assert tools == [
        "extract_google_sheets",
        "run_data_ops_plan",
        "map_fields",
        "start_migration",
    ]
    data_step = next(s for s in plan.steps if s.parameters.get("tool_id") == "run_data_ops_plan")
    assert data_step.depends_on == ["extract"]
    op_ids = [x["operation_id"] for x in data_step.parameters.get("steps") or []]
    assert "dedupe" in op_ids
    assert "filter" in op_ids
    map_step = next(s for s in plan.steps if s.parameters.get("tool_id") == "map_fields")
    assert map_step.depends_on == ["data_ops"]


def test_extract_map_migrate_without_data_ops_unchanged():
    classification = {
        "source": "sql",
        "destination": "sql",
        "source_name": "Test DB",
        "destination_name": "Test DB",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
        },
    }
    prompt = "extract customer data from Test DB and migrate to same database"
    plan = build_plan_steps(classification, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_sql", "map_fields", "start_migration"]


def test_enrichment_preserves_classifier_data_operations():
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "source_name": "contact list",
        "destination_name": "contact list",
        "intent": "dedupe",
        "mode": "data_ops",
        "data_operations": ["dedupe", "filter"],
    }
    out = enrich_classification_for_agent_runtime(classification, None, prompt=GOLDEN_PROMPT)
    assert out["mode"] == "data_ops"
    assert out.get("data_operations") == ["dedupe", "filter"]


def test_resolve_data_operations_continuation_remove_empty_records():
    cls = {
        "_continuation_steps": ["data_ops"],
        "active_job_id": "job-1",
        "_plan_scope": {"data_operations": ["filter"]},
    }
    ops = resolve_data_operations(cls, "remove record where first name and email is empty")
    assert ops == ["filter"]


def test_resolve_data_operations_multi_op_order_preserved():
    cls = {
        "_continuation_steps": ["data_ops"],
        "_plan_scope": {"data_operations": ["dedupe", "filter"]},
    }
    ops = resolve_data_operations(cls, "dedupe and remove invalid emails")
    assert ops.index("dedupe") < ops.index("filter")


def test_standalone_data_ops_plan_has_no_destination():
    classification = {
        "source": "salesforce",
        "source_name": "SF 1",
        "source_object_api_name": "Contact",
        "active_job_id": "job-1",
        "mode": "data_ops",
        "_continuation_steps": ["data_ops"],
        "_plan_scope": {"data_operations": ["transform"]},
    }
    plan = build_plan_steps(
        classification,
        prompt="split name to first name and last name",
    )
    assert plan.metadata.get("pipeline_label") == "data_ops"
    assert plan.destination is None
    assert [s.parameters.get("tool_id") for s in plan.steps] == ["run_data_ops_plan"]
    op_ids = [x["operation_id"] for x in plan.steps[0].parameters.get("steps") or []]
    assert op_ids == ["transform"]


def test_standalone_data_ops_no_default_transform_without_prompt_ops():
    classification = {
        "source": "salesforce",
        "active_job_id": "job-1",
        "mode": "data_ops",
        "_continuation_steps": ["data_ops"],
        "_plan_scope": {"operations": [], "data_operations": []},
    }
    plan = build_plan_steps(classification, prompt="show me the data")
    assert plan.steps == []
