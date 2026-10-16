"""Regression: column combine + migrate must not trigger join or create-spreadsheet plans."""

from services.planning.plan_builder import build_plan_steps

SERVICE_APPOINTMENTS_PROMPT = (
    "Extract the 100 records from the Service Appointments sheet, combine DEALERCODE VC, "
    "DEALERBRANCHCODE VC, CUSTCODE VC column as Customer Account, and combine DEALERCODE VC, "
    "DEALERBRANCHCODE VC, CRONO VC as CRO User and migrate it to the Service Appointment "
    "object of the SF 1 Salesforce org"
)

SERVICE_APPOINTMENTS_PROMPT_V2 = (
    "Extract the 100 records from the Service Appointments sheet, from the extarcted data "
    "combine DEALERCODE VC, DEALERBRANCHCODE VC, CUSTCODE VC column as Customer Account, "
    "and combine DEALERCODE VC, DEALERBRANCHCODE VC, CRONO VC as CRO User and migrate it to "
    "the Service Appointment object of the SF 1 Salesforce org"
)


def test_column_combine_not_join_plan():
    for prompt in (SERVICE_APPOINTMENTS_PROMPT, SERVICE_APPOINTMENTS_PROMPT_V2):
        plan = build_plan_steps(
            classification={
                "intent": "extract",
                "source": "google_sheets",
                "destination": "salesforce",
                "_plan_scope": {"operations": ["extract", "transform", "map", "migrate"]},
            },
            prompt=prompt,
        )
        tools = [s.parameters.get("tool_id") for s in plan.steps]
        assert "gsheets_execute_operation" not in tools


def test_workbook_combine_uses_multi_source_plan():
    plan = build_plan_steps(
        classification={
            "intent": "extract",
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "google_sheets",
            "destination_name": "contact list",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "data list workbook"}
            ],
            "multi_source_pipeline": True,
            "_plan_scope": {"operations": ["extract", "join", "map", "migrate"]},
        },
        prompt="extract from SF 1 and data list workbook, combine them and export to contact list sheet",
    )
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert "gsheets_execute_operation" in tools


def test_workbook_combine_plan_has_join_step_not_legacy_join_op():
    plan = build_plan_steps(
        classification={
            "intent": "extract",
            "source": "salesforce",
            "source_name": "SF 1",
            "destination": "google_sheets",
            "destination_name": "contact list",
            "planner_additional_sources": [
                {"connector_type": "google_sheets", "display_name": "data list workbook"}
            ],
            "multi_source_pipeline": True,
            "_plan_scope": {"operations": ["extract", "join", "map", "migrate"]},
        },
        prompt="extract from SF 1 and data list workbook, combine them and export to contact list sheet",
    )
    join_steps = [s for s in plan.steps if s.parameters.get("operation") == "join"]
    assert join_steps


def test_enrich_runtime_keeps_extract_for_compound_migrate():
    from handlers.classification_handler import enrich_classification_for_agent_runtime

    out = enrich_classification_for_agent_runtime(
        {
            "intent": "extract",
            "mode": "extract",
            "source": "google_sheets",
            "destination": "salesforce",
            "source_name": "Service Appointments",
            "destination_name": "SF 1",
        },
        None,
        prompt=SERVICE_APPOINTMENTS_PROMPT,
    )
    assert out["intent"] == "extract"
    assert out.get("mode") != "join"


def test_gsheets_to_sf_combine_plan_is_extract_map_migrate():
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "source_name": "Service Appointments",
        "destination_name": "SF 1",
        "intent": "extract",
        "mode": "extract",
        "planner_create_spreadsheet": True,
    }
    goal = {
        "intent": "extract",
        "operations": ["extract", "map", "migrate", "transform"],
        "create_destination": True,
        "data_operations": ["transform"],
    }
    classification["_plan_scope"] = goal
    plan = build_plan_steps(classification, prompt=SERVICE_APPOINTMENTS_PROMPT)
    tools = [s.parameters.get("tool_id") for s in plan.steps]
    assert plan.metadata.get("pipeline_label") == "extract → data_ops → map → migrate"
    assert "extract_google_sheets" in tools
    assert "google_sheets_create_from_prompt" not in tools
    assert "run_data_ops_plan" in tools
    assert "map_fields" in tools
    assert "start_migration" in tools
    migrate = next(s for s in plan.steps if s.parameters.get("tool_id") == "start_migration")
    assert "SF 1" in (migrate.label or "")


def test_migrate_dest_guards_clear_create_spreadsheet_flag():
    from services.planning.connector_guards import apply_migrate_destination_guards

    classification = {
        "destination": "salesforce",
        "destination_name": "SF 1",
        "planner_create_spreadsheet": True,
        "planner_new_spreadsheet_name": "Service Appointments",
    }
    goal = {"create_destination": True, "operations": ["extract", "map", "migrate"]}
    out, parsed = apply_migrate_destination_guards(
        classification, goal, SERVICE_APPOINTMENTS_PROMPT
    )
    assert out.get("planner_create_spreadsheet") is False
    assert parsed.get("create_destination") is False
