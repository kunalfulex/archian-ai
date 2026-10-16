"""Tests for clarification apply preserving migration intent across connector picks."""
from services.planning.clarification_apply import apply_clarification_selections
from services.planning.migration_intent_schemas import PromptMigrationIntent
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def test_sql_connector_pick_preserves_migration_intent():
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="users",
        driving_entity_label="users",
    )
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="sql-1",
                clarification_type="pick_sql_connector",
                prompt="Which SQL connection?",
                options=[
                    ClarificationOption(
                        id="sql_source_test_db",
                        label="Test DB",
                        resource_ref=ResourceRef(
                            connector_type="sql",
                            display_name="Test DB",
                            connector_id="sql-1",
                        ),
                    )
                ],
            )
        ],
        metadata={"migration_intent": intent.model_dump(mode="json")},
    )
    cls = {
        "source": "sql",
        "destination": "salesforce",
        "intent": "migrate",
    }
    out, updated = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": "sql-1", "option_id": "sql_source_test_db"}],
    )
    assert out["source_name"] == "Test DB"
    assert out["source_connector_id"] == "sql-1"
    assert updated.metadata.get("migration_intent")
    assert updated.metadata["migration_intent"]["driving_entity_key"] == "users"


def test_entity_pick_updates_migration_intent_instead_of_clearing():
    intent = PromptMigrationIntent(
        cross_system=True,
        driving_entity_key="accounts",
        driving_entity_label="accounts",
    )
    plan = ExecutionPlan(
        plan_id="p1",
        clarifications=[
            Clarification(
                id="ent-1",
                clarification_type="pick_source_entity",
                prompt="Which table?",
                options=[],
            )
        ],
        metadata={"migration_intent": intent.model_dump(mode="json")},
    )
    cls = {
        "source": "sql",
        "source_name": "Test DB",
        "source_connector_id": "sql-1",
        "_source_endpoint_clarified": True,
    }
    out, updated = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": "ent-1", "option_id": "source:users"}],
    )
    assert out["source_table_name"] == "users"
    assert out["planner_source_entity_confirmed"] is True
    assert updated.metadata["migration_intent"]["driving_entity_key"] == "users"


def test_sql_clarify_then_entity_pick_preserves_create_contract_pins():
    """Clarify → resume keeps step contract pins on plan metadata for execute."""
    from agent.session import AgentSession
    from services.planning.plan_runner import _arguments_for_tool

    plan = ExecutionPlan(
        plan_id="p-create",
        status="awaiting_clarification",
        steps=[],
        clarifications=[
            Clarification(
                id="sql-1",
                clarification_type="pick_sql_connector",
                prompt="Which SQL connection?",
                options=[
                    ClarificationOption(
                        id="sql_source_test_db",
                        label="Test DB",
                        resource_ref=ResourceRef(
                            connector_type="sql",
                            display_name="Test DB",
                            connector_id="sql-1",
                        ),
                    )
                ],
            )
        ],
        metadata={
            "migration_intent": PromptMigrationIntent(
                cross_system=True,
                driving_entity_key="users",
                driving_entity_label="users",
            ).model_dump(mode="json"),
            "create_contract": {
                "spreadsheet_title": "Export",
                "headers": ["Name", "Email"],
                "create_destination": True,
            },
        },
    )
    cls = {
        "source": "sql",
        "destination": "google_sheets",
        "intent": "migrate",
        "planner_new_spreadsheet_name": "Export",
        "planner_spreadsheet_headers": ["Name", "Email"],
        "_plan_scope": {"operations": ["extract", "create", "map", "migrate"]},
    }
    out, updated = apply_clarification_selections(
        cls,
        plan,
        [{"clarification_id": "sql-1", "option_id": "sql_source_test_db"}],
    )
    assert updated.metadata.get("create_contract", {}).get("spreadsheet_title") == "Export"
    assert updated.metadata["migration_intent"]["driving_entity_key"] == "users"

    session = AgentSession(
        user_id="u",
        conversation_id=None,
        mode="extract",
        classification_snapshot={**out, "execution_plan": updated.model_dump(mode="json")},
    )
    args = _arguments_for_tool(
        "google_sheets_create_from_prompt",
        session,
        {},
        {"spreadsheet_title": "Export"},
    )
    contract = args.get("create_contract") or {}
    assert contract.get("spreadsheet_title") == "Export"
    assert contract.get("headers") == ["Name", "Email"]
