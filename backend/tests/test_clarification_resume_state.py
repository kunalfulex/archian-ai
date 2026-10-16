"""Regression: column picks must not reset source SQL connection."""
from services.planning.clarification_apply import (
    _restore_planner_endpoint_pins,
    apply_clarification_selections,
)
from services.planning.clarification_sync import sync_plan_clarifications
from services.planning.migration_intent_schemas import ExtractAmbiguity, PromptMigrationIntent
from services.planning.resource_catalog import ResourceCatalog, SqlConnectionRef
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def test_restore_planner_endpoint_pins_keeps_sql_connection():
    prior = {
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "sql-1",
        "_source_endpoint_clarified": True,
    }
    out = {"source": "sql", "extract_fields_confirmed": True}
    merged, _ = _restore_planner_endpoint_pins(out, prior)
    assert merged["source_name"] == "Test DB"
    assert merged["_source_endpoint_clarified"] is True


def test_restore_planner_endpoint_pins_handles_operations_list():
    prior = {
        "source": "google_sheets",
        "source_connector_id": "gs-1",
        "operations": ["extract", "map", "migrate"],
        "_plan_scope": {
            "operations": ["extract", "map", "migrate"],
            "needs_source": True,
            "needs_destination": True,
        },
    }
    out = {
        "source": "google_sheets",
        "destination": "google_sheets",
        "destination_name": "contact list",
    }
    merged, _ = _restore_planner_endpoint_pins(out, prior)
    assert merged["operations"] == ["extract", "map", "migrate"]
    assert merged["source_connector_id"] == "gs-1"


def test_field_pick_does_not_reopen_sql_connector_gap():
    catalog = ResourceCatalog(
        sql_connections=[
            SqlConnectionRef(name="Test DB", connector_id="sql-1"),
            SqlConnectionRef(name="UAT DB", connector_id="sql-2"),
        ]
    )
    plan = ExecutionPlan(
        plan_id="p-fields",
        status="awaiting_clarification",
        metadata={
            "migration_intent": PromptMigrationIntent(
                cross_system=True,
                driving_entity_key="users",
                driving_entity_label="users",
                suggested_field_hints=["email", "name"],
            ).model_dump(mode="json")
        },
        clarifications=[
            Clarification(
                id="sql-pick",
                clarification_type="pick_sql_connector",
                prompt="Which SQL connection?",
                options=[
                    ClarificationOption(
                        id="sql_source_Test DB",
                        label="Test DB",
                        resource_ref=ResourceRef(
                            connector_type="sql",
                            display_name="Test DB",
                            connector_id="sql-1",
                        ),
                    ),
                ],
                selected_option_id="sql_source_Test DB",
            ),
            Clarification(
                id="field-pick",
                clarification_type="pick_extract_fields",
                prompt="Which columns?",
                options=[
                    ClarificationOption(id="field_hint::email", label="email"),
                    ClarificationOption(id="field_hint::name", label="name"),
                ],
            ),
        ],
    )
    classification = {
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "sql-1",
        "_source_endpoint_clarified": True,
        "destination": "salesforce",
        "intent": "migrate_data",
        "operations": ["extract", "migrate"],
    }
    out, updated = apply_clarification_selections(
        classification,
        plan,
        [
            {"clarification_id": "field-pick", "option_id": "field_hint::email"},
            {"clarification_id": "field-pick", "option_id": "field_hint::name"},
        ],
    )
    assert out.get("extract_fields_confirmed") is True
    assert out["_source_endpoint_clarified"] is True

    import asyncio

    from services.planning.op_requirements import read_plan_requirements
    from services.planning.plan_scope import read_plan_scope

    parsed_goal = read_plan_scope(out)
    reqs = read_plan_requirements(out, parsed_goal=parsed_goal)
    updated = asyncio.run(
        sync_plan_clarifications(
            updated,
            out,
            user_id="user-1",
            requirements=reqs,
            parsed_goal=parsed_goal,
            catalog=catalog,
            prompt="deploy user data to salesforce",
            advance_one=True,
        )
    )
    blocking_types = [c.clarification_type for c in updated.blocking_clarifications()]
    assert "pick_sql_connector" not in blocking_types
