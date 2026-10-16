from services.planning.plan_builder import build_plan_steps, finalize_plan_status
from services.planning.schemas import (
    ExecutionPlan,
    PlanStep,
    ResourceRef,
    StepType,
    normalize_classification_connectors,
)
from tests.plan_scope_helpers import assert_pipeline_steps, with_plan_scope


def test_finalize_plan_status_awaiting_by_default():
    plan = ExecutionPlan(
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract", parameters={"tool_id": "extract_salesforce"})],
    )
    plan = finalize_plan_status(plan)
    assert plan.status == "awaiting_approval"


def test_finalize_plan_status_auto_run_approves():
    plan = ExecutionPlan(
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract", parameters={"tool_id": "extract_salesforce"})],
    )
    plan = finalize_plan_status(plan, plan_auto_run=True)
    assert plan.status == "approved"


def test_finalize_plan_status_force_auto_approve():
    plan = ExecutionPlan(
        steps=[PlanStep(step_type=StepType.EXTRACT, label="Extract", parameters={"tool_id": "extract_salesforce"})],
    )
    plan = finalize_plan_status(plan, force_auto_approve=True)
    assert plan.status == "approved"


def test_sf_to_new_sheet_plan_steps():
    classification = with_plan_scope(
        {
            "source": "salesforce",
            "destination": "google_sheets",
            "source_name": "SF 1",
            "intent": "extract",
            "mode": "extract",
            "planner_create_spreadsheet": True,
            "planner_new_spreadsheet_name": "CRM contact list",
            "planner_destination_confirmed": True,
        },
        operations=["extract", "create", "map", "migrate"],
        create_destination=True,
    )
    plan = build_plan_steps(classification, prompt="extract contacts and export to google sheets")
    plan = finalize_plan_status(plan)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "extract_salesforce" in tools
    assert "google_sheets_create_from_prompt" in tools
    assert "map_fields" in tools
    assert "start_migration" in tools
    assert_pipeline_steps(plan, ["extract", "create", "map", "migrate"])


def test_load_flow_run_template_starts_with_load_flow():
    classification = {
        "flow_id": "abc-123",
        "load_flow": True,
        "source": "salesforce",
        "destination": "google_sheets",
        "_plan_scope": {"operations": ["extract", "migrate"], "intent": "extract"},
    }
    plan = build_plan_steps(classification, prompt="run my export flow")
    assert plan.steps[0].parameters.get("tool_id") == "load_flow"


def test_sf_to_sf_map_skips_schema_gate_without_custom_fields(monkeypatch):
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "Production",
        "destination_name": "Sandbox",
        "intent": "map_data",
        "mode": "map",
        "source_schema": ["Email", "FirstName", "LastName"],
    }
    goal = {"intent": "map_data", "operations": ["map", "migrate"]}
    classification["_plan_scope"] = goal
    plan = build_plan_steps(classification, prompt="map contacts from Production to Sandbox")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "schema_gate_assess" not in tools
    assert tools[0] == "map_fields"


def test_sf_to_sf_map_includes_schema_gate_steps_for_custom_fields(monkeypatch):
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "Production",
        "destination_name": "Sandbox",
        "intent": "map_data",
        "mode": "map",
        "source_schema": ["Email", "Custom_Score__c"],
    }
    goal = {"intent": "map_data", "operations": ["map", "migrate"]}
    classification["_plan_scope"] = goal
    plan = build_plan_steps(classification, prompt="map contacts from Production to Sandbox")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools[0] == "schema_gate_assess"
    assert tools[1] == "schema_gate_propose"
    assert tools[2] == "metadata_deploy_salesforce"
    assert "map_fields" in tools
    map_step = next(s for s in plan.steps if s.parameters.get("tool_id") == "map_fields")
    assert "deploy_schema" in (map_step.depends_on or [])


def test_migrate_only_skips_schema_gate(monkeypatch):
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "destination_name": "SF 2",
        "intent": "migrate",
        "mode": "migrate",
        "active_job_id": "job-1",
        "source_schema": ["Custom_Score__c"],
        "_session_progress": {"mapping_ready": True},
        "last_completed_step": "map",
    }
    classification["_plan_scope"] = {"intent": "migrate", "operations": ["migrate"]}
    plan = build_plan_steps(classification, prompt="start the migration")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["start_migration"]


def test_multi_source_extract_plan():
    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_name": "Production",
        "destination_name": "Combined",
        "planner_additional_sources": [
            {"connector_type": "sql", "display_name": "Warehouse DB"},
        ],
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "intent": "extract"},
    }
    plan = build_plan_steps(classification, prompt="extract from both sources and export")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools.count("extract_salesforce") >= 1
    assert "extract_sql" in tools
    assert "start_migration" in tools
    assert any(s.label.startswith("Export to Combined") for s in plan.steps)
    assert_pipeline_steps(plan, ["extract", "map", "migrate"])


def test_multi_source_gsheets_to_gsheets_includes_export():
    """Same connector type for source and destination must still show export step."""
    classification = {
        "source": "google_sheets",
        "source_name": "data list workbook",
        "destination": "google_sheets",
        "destination_name": "contact list",
        "destination_sheet_name": "Sheet1",
        "planner_additional_sources": [
            {"connector_type": "salesforce", "display_name": "SF 1"},
        ],
    }
    plan = build_plan_steps(
        classification,
        prompt="extract from SF 1 and data list workbook, combine and export to contact list sheet",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    labels = [s.label for s in plan.steps]
    assert labels[0].startswith("Extract from ")
    assert "→" not in " ".join(labels)
    assert any(lbl.startswith("Combine ") for lbl in labels)
    assert "map_fields" in tools
    assert tools.index("map_fields") < tools.index("start_migration")
    assert labels[-1].startswith("Export to contact list")
    assert "contact list" in labels[-1]
    assert plan.goal_summary
    assert "combine" in plan.goal_summary.lower()
    assert "map fields" in plan.goal_summary.lower()
    assert "export" in plan.goal_summary.lower()
    assert plan.destination is not None
    assert plan.destination.display_name == "contact list"


def test_map_continuation_without_named_destination_has_no_plan_steps():
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "active_job_id": "job-abc",
        "intent": "map_data",
        "mode": "map",
        "_plan_scope": {"operations": ["map"], "intent": "map_data"},
    }
    plan = build_plan_steps(classification, prompt="map the data")
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == []
    assert plan.metadata.get("pipeline_label") in ("map", "empty")


def test_compound_prompt_with_stale_active_job_builds_full_pipeline():
    from services.planning.plan_builder import build_plan_steps, finalize_plan_status

    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "destination_name": "SF 2",
        "active_job_id": "old-job",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    prompt = (
        "extract first name, last name, email from contact object SF 1 org "
        "and migrate them to SF 2 org"
    )
    plan = build_plan_steps(classification, prompt=prompt)
    plan = finalize_plan_status(plan, plan_auto_run=True)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_salesforce", "map_fields", "start_migration"]


def test_sf_cross_org_extract_map_migrate_plan():
    """SF org A → org B with extract+migrate in one prompt must not stop after extract only."""
    classification = {
        "source": "salesforce",
        "destination": "salesforce",
        "source_name": "SF 1",
        "destination_name": "SF 2",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    prompt = (
        "extract first name, last name, email from contact object SF 1 org "
        "and migrate them to SF 2 org"
    )
    plan = build_plan_steps(classification, prompt=prompt)
    plan = finalize_plan_status(plan, plan_auto_run=True)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_salesforce", "map_fields", "start_migration"]


def test_swapped_sf_orgs_continuation_keeps_job_pins():
    """Without keyword overrides, continuation preserves extraction job connector pins."""
    from services.planning.continuation_policy import apply_extraction_session_continuation
    from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef

    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    prompt = (
        "extract first name, last name, email, account name and account phone "
        "from contact object SF 2 org and migrate them to SF 1 org"
    )
    llm = {
        "source": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "intent": "extract",
        "mode": "extract",
    }
    extraction_meta = {
        "source_type": "salesforce",
        "source_name": "SF 1",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "job_id": "71ffa9d1-12a6-4aac-a011-2dafc4f8444f",
    }
    out = apply_extraction_session_continuation(
        llm,
        extraction_meta=extraction_meta,
        prompt=prompt,
        catalog=cat,
    )
    out["_plan_scope"] = {"operations": ["extract", "map", "migrate"]}
    plan = build_plan_steps(out, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert out["source_name"] == "SF 1"
    assert out["destination_name"] == "SF 2"
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_salesforce", "map_fields", "start_migration"]


def test_sql_cross_db_extract_map_migrate_plan():
    from services.planning.continuation_policy import apply_extraction_session_continuation
    from services.planning.resource_catalog import ResourceCatalog, SqlConnectionRef

    cat = ResourceCatalog(
        sql_connections=[
            SqlConnectionRef(name="Warehouse DB"),
            SqlConnectionRef(name="Analytics DB"),
        ]
    )
    prompt = "extract customer data from Warehouse DB and migrate to Analytics DB"
    llm = {
        "source": "sql",
        "source_name": "Analytics DB",
        "destination": "sql",
        "destination_name": "Warehouse DB",
        "intent": "extract",
        "mode": "extract",
    }
    extraction_meta = {
        "source_type": "sql",
        "source_name": "Analytics DB",
        "destination": "sql",
        "destination_name": "Warehouse DB",
        "job_id": "job-stale",
    }
    out = apply_extraction_session_continuation(
        llm, extraction_meta=extraction_meta, prompt=prompt, catalog=cat
    )
    out["_plan_scope"] = {"operations": ["extract", "map", "migrate"]}
    plan = build_plan_steps(out, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert out["source_name"] == "Analytics DB"
    assert out["destination_name"] == "Warehouse DB"
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert plan.metadata.get("extraction_profile") == "source_true_migrate"
    assert tools == ["extract_sql", "map_fields", "start_migration"]


def test_sql_same_db_compound_full_pipeline():
    """Same DB extract+migrate in one prompt must run extract → map → migrate (goal ops driven)."""
    classification = {
        "source": "sql",
        "destination": "sql",
        "source_name": "Test DB",
        "destination_name": "Test DB",
        "destination_same_as_source": True,
        "intent": "extract",
        "mode": "extract",
        "active_job_id": None,
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    prompt = "extract customer data from Test DB and migrate into same database"
    plan = build_plan_steps(classification, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_sql", "map_fields", "start_migration"]


def test_gs_same_workbook_compound_full_pipeline():
    classification = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "source_name": "contact list",
        "destination_name": "contact list",
        "destination_same_as_source": True,
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {"operations": ["extract", "map", "migrate"]},
    }
    prompt = "extract data from contact list and migrate to the same sheet"
    plan = build_plan_steps(classification, prompt=prompt)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_google_sheets", "map_fields", "start_migration"]


def test_sql_same_db_compound_not_cross_endpoint():
    from services.planning.plan_builder import _route_endpoints_differ

    classification = {
        "source_name": "Test DB",
        "destination_name": "Test DB",
    }
    assert not _route_endpoints_differ(classification, "sql", "sql")


def test_extract_unknown_destination_omits_export_step():
    classification = {
        "source": "salesforce",
        "destination": "unknown",
        "source_name": "SF 1",
        "intent": "extract",
        "_plan_scope": {"operations": ["extract"], "intent": "extract"},
    }
    plan = build_plan_steps(
        classification,
        prompt="extract first name, last name, email from contact object SF 1 org",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools == ["extract_salesforce"]
    assert plan.metadata.get("pipeline_label") == "extract"


def test_gsheets_source_extract_only():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "source_name": "Employee list",
        "intent": "extract",
    }
    plan = build_plan_steps(classification)
    assert plan.steps[0].parameters.get("tool_id") == "extract_google_sheets"


def test_resource_ref_accepts_gsheets_alias():
    ref = ResourceRef(connector_type="gsheets", display_name="Service Appointments")
    assert ref.connector_type == "google_sheets"


def test_gsheets_alias_source_map_plan_does_not_raise():
    """Regression: intent_state active_source=gsheets must not break planner ResourceRef validation."""
    classification = {
        "source": "gsheets",
        "destination": "salesforce",
        "source_name": "Service Appointments",
        "destination_name": "SF 1",
        "intent": "map_data",
        "mode": "map",
        "active_job_id": "job-abc",
    }
    plan = build_plan_steps(
        classification,
        prompt="map data to SF 1 salesforce org, Service Appointment object",
    )
    assert plan.sources[0].connector_type == "google_sheets"
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert tools[0] == "map_fields"


def test_normalize_classification_connectors():
    out = normalize_classification_connectors(
        {
            "source": "gsheets",
            "destination": "salesforce",
            "extract_sources": [{"connector_type": "gsheets", "display_name": "Book1"}],
        }
    )
    assert out["source"] == "google_sheets"
    assert out["extract_sources"][0]["connector_type"] == "google_sheets"


def test_continuation_map_migrate_skips_extract():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "source_name": "Service Appointments",
        "destination_name": "SF 1",
        "intent": "map_data",
        "mode": "map",
        "active_job_id": "job-existing",
        "plan_route": "continuation_path",
        "_continuation_steps": ["map", "migrate"],
        "_plan_scope": {"operations": ["map", "migrate"], "intent": "map_data"},
    }
    plan = build_plan_steps(
        classification,
        prompt="map to SF 1 salesforce org, Service Appointment object",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "map → migrate"
    assert tools == ["map_fields", "start_migration"]
    assert plan.steps[0].parameters.get("destination_name") == "SF 1"


def test_fresh_full_pipeline_keeps_extract():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "source_name": "Service Appointments",
        "destination_name": "SF 1",
        "intent": "extract",
        "mode": "extract",
        "_plan_scope": {"operations": ["extract", "map", "migrate"], "intent": "extract"},
    }
    plan = build_plan_steps(
        classification,
        prompt="extract from service appointments and migrate to SF 1",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → map → migrate"
    assert tools == ["extract_google_sheets", "map_fields", "start_migration"]
