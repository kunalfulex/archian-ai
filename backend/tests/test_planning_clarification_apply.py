"""Tests for planner clarification apply + resume."""
from services.planning.clarification_apply import (
    apply_clarification_selections,
    build_planner_pending_payload,
)
from services.planning.schemas import merge_plan_into_classification
from services.planning.migration_intent_schemas import EntityMention, ExtractAmbiguity, PromptMigrationIntent
from services.planning.plan_builder import build_plan_steps
from services.planning.schemas import Clarification, ClarificationOption, ExecutionPlan, ResourceRef


def test_apply_extract_related_vs_secondary_clarification():
    plan = ExecutionPlan(
        plan_id="p-extract",
        status="awaiting_clarification",
        metadata={
            "migration_intent": PromptMigrationIntent(
                extract_shape="ambiguous",
                needs_extract_clarification=True,
                entity_mentions=[
                    EntityMention(phrase="contacts", role="extract_primary"),
                    EntityMention(phrase="account", role="ambiguous", confidence=0.5),
                ],
                extract_ambiguities=[
                    ExtractAmbiguity(
                        ambiguity_type="extract_related_vs_secondary",
                        prompt="Related or separate?",
                    )
                ],
            ).model_dump(mode="json")
        },
        clarifications=[
            Clarification(
                id="c-related",
                clarification_type="extract_related_vs_secondary",
                prompt="Related or separate?",
                options=[
                    ClarificationOption(
                        id="related_fields",
                        label="Related fields on the primary object (one query)",
                    ),
                    ClarificationOption(
                        id="separate_extract",
                        label="Separate extract for the second object",
                    ),
                ],
            ),
        ],
    )
    out, updated = apply_clarification_selections(
        {"source": "salesforce", "destination": "salesforce"},
        plan,
        [{"clarification_id": "c-related", "option_id": "related_fields"}],
    )
    intent = updated.metadata["migration_intent"]
    assert intent["include_related_data"] is True
    assert intent.get("related_entity_phrase") == "account"
    assert intent["needs_extract_clarification"] is False
    assert out.get("_extract_intent_clarified") is True


def test_apply_pick_workbook_sets_destination():
    plan = ExecutionPlan(
        plan_id="p1",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c1",
                clarification_type="pick_workbook",
                prompt="Which sheet?",
                options=[
                    ClarificationOption(
                        id="gs_MyBook",
                        label="MyBook (Sheet1)",
                        resource_ref=ResourceRef(
                            connector_type="google_sheets",
                            display_name="MyBook",
                            spreadsheet_id="abc123",
                            sheet_name="Sheet1",
                        ),
                    ),
                ],
            ),
        ],
    )
    classification = {
        "source": "sql",
        "destination": "google_sheets",
        "intent": "map_data",
    }
    out, updated = apply_clarification_selections(
        classification,
        plan,
        [{"clarification_id": "c1", "option_id": "gs_MyBook"}],
    )
    assert out["destination_name"] == "MyBook"
    assert out["destination_spreadsheet_id"] == "abc123"
    assert updated.clarifications[0].selected_option_id == "gs_MyBook"
    assert not updated.blocking_clarifications()


def test_apply_create_new_spreadsheet_flag():
    plan = ExecutionPlan(
        clarifications=[
            Clarification(
                id="c2",
                clarification_type="pick_workbook",
                prompt="Which sheet?",
                options=[
                    ClarificationOption(
                        id="gs_create_new",
                        label="Create new spreadsheet",
                        create_new=True,
                    ),
                ],
            ),
        ],
    )
    out, _ = apply_clarification_selections(
        {"destination": "google_sheets"},
        plan,
        [{"clarification_id": "c2", "option_id": "gs_create_new"}],
    )
    assert out.get("planner_create_spreadsheet") is True
    assert out.get("destination_name") is None
    assert out.get("planner_destination_confirmed") is False


def test_apply_pick_workbook_ambiguous_sets_source_for_gsheets_to_salesforce():
    plan = ExecutionPlan(
        clarifications=[
            Clarification(
                id="amb1",
                clarification_type="pick_workbook_ambiguous",
                prompt='Which spreadsheet for "contact list"?',
                options=[
                    ClarificationOption(
                        id="gs_amb_abc_Sheet1",
                        label="contact list (Sheet1) · …abc123",
                        resource_ref=ResourceRef(
                            connector_type="google_sheets",
                            display_name="contact list",
                            spreadsheet_id="spreadsheet-abc123",
                            sheet_name="Sheet1",
                        ),
                    ),
                ],
            ),
        ],
    )
    classification = {
        "source": "google_sheets",
        "destination": "salesforce",
        "destination_name": "SF 2",
        "_endpoint_ambiguous": [{"phrase": "contact list", "candidates": []}],
    }
    out, updated = apply_clarification_selections(
        classification,
        plan,
        [{"clarification_id": "amb1", "option_id": "gs_amb_abc_Sheet1"}],
    )
    assert out["source_name"] == "contact list"
    assert out["source_spreadsheet_id"] == "spreadsheet-abc123"
    assert out.get("_endpoint_ambiguous") is None
    assert updated.clarifications[0].selected_option_id == "gs_amb_abc_Sheet1"


def test_apply_pick_existing_workbook_clears_create_flag_and_plan_has_no_create_step():
    plan = ExecutionPlan(
        clarifications=[
            Clarification(
                id="c3",
                clarification_type="pick_workbook",
                prompt="Which sheet?",
                options=[
                    ClarificationOption(
                        id="gs_contact_list",
                        label="contact list (Sheet1)",
                        resource_ref=ResourceRef(
                            connector_type="google_sheets",
                            display_name="contact list",
                            spreadsheet_id="1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA",
                            sheet_name="Sheet1",
                        ),
                    ),
                ],
            ),
        ],
    )
    classification = {
        "source": "salesforce",
        "destination": "google_sheets",
        "source_name": "SF 1",
        "intent": "extract",
        "mode": "extract",
        "planner_create_spreadsheet": True,
        "operations": ["extract", "map", "migrate"],
    }
    out, _ = apply_clarification_selections(
        classification,
        plan,
        [{"clarification_id": "c3", "option_id": "gs_contact_list"}],
    )
    assert out.get("planner_create_spreadsheet") is False
    assert out.get("destination_spreadsheet_id") == "1qzAhsrz9wugQeIUsAdnV2yvULuA43lCcyxQjcnqi9MA"
    assert out.get("destination_name") == "contact list"
    assert out.get("planner_destination_entity_confirmed") is True
    out["_plan_scope"] = {
        "operations": ["extract", "map", "migrate"],
        "create_destination": True,
    }
    built = build_plan_steps(out, prompt="extract contacts and deploy to google sheet")
    tools = [s.parameters.get("tool_id") for s in built.steps]
    assert "google_sheets_create_from_prompt" not in tools
    assert "extract_salesforce" in tools
    assert "map_fields" in tools
    assert "start_migration" in tools


def test_merge_plan_exports_only_blocking_clarifications():
    plan = ExecutionPlan(
        plan_id="p-block",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="done",
                clarification_type="pick_source_connector",
                prompt="Where from?",
                options=[],
                selected_option_id="src_type_sql",
            ),
            Clarification(
                id="open",
                clarification_type="pick_sql_connector",
                prompt="Which SQL connection?",
                options=[
                    ClarificationOption(
                        id="sql_source_Test DB",
                        label="Test DB",
                        resource_ref=ResourceRef(connector_type="sql", display_name="Test DB"),
                    )
                ],
            ),
        ],
    )
    merged = merge_plan_into_classification({"source": "sql"}, plan)
    assert merged["needs_clarification"] is True
    assert len(merged["clarifications"]) == 1
    assert merged["clarifications"][0]["id"] == "open"


def test_pick_sql_clears_stale_migration_intent():
    plan = ExecutionPlan(
        metadata={
            "migration_intent": {
                "driving_entity_key": "RecordType",
                "driving_entity_label": "Record Types",
            }
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
                            connector_id="sql-99",
                        ),
                    )
                ],
            ),
        ],
    )
    out, updated = apply_clarification_selections(
        {"source": "sql", "migration_intent": {"driving_entity_key": "RecordType"}},
        plan,
        [{"clarification_id": "sql-pick", "option_id": "sql_source_Test DB"}],
    )
    assert out["source_name"] == "Test DB"
    assert out["source_connector_id"] == "sql-99"
    assert "migration_intent" not in updated.metadata
    assert "migration_intent" not in out


def test_build_planner_pending_includes_intent_state():
    pending = build_planner_pending_payload(
        {
            "intent_state": {"turn_type": "migrate", "source": {"connector_type": "salesforce"}},
            "plan_id": "p1",
        },
        prompt="migrate contacts",
        conversation_id="conv-1",
    )
    assert pending["intent_state"]["turn_type"] == "migrate"
    assert pending["classification"]["intent_state"]["turn_type"] == "migrate"


def test_build_planner_pending_blocking_only():
    plan = ExecutionPlan(
        clarifications=[
            Clarification(
                id="done",
                clarification_type="pick_source_connector",
                prompt="done",
                options=[],
                selected_option_id="x",
            ),
            Clarification(
                id="open",
                clarification_type="pick_sql_connector",
                prompt="open",
                options=[],
            ),
        ],
    )
    pending = build_planner_pending_payload(
        {
            "clarifications": [c.model_dump(mode="json") for c in plan.clarifications],
            "execution_plan": plan.model_dump(mode="json"),
        },
        prompt="deploy user data",
    )
    assert len(pending["clarifications"]) == 1
    assert pending["clarifications"][0]["id"] == "open"


def test_apply_pick_destination_entity_sets_salesforce_object():
    plan = ExecutionPlan(
        plan_id="p-dest-entity",
        status="awaiting_clarification",
        metadata={"plan_contract": {"destination": {"entity_key": "RecordType"}}},
        clarifications=[
            Clarification(
                id="c-dest",
                clarification_type="pick_destination_entity",
                prompt="Which object?",
                options=[
                    ClarificationOption(id="destination:User", label="User"),
                ],
            ),
        ],
    )
    out, updated = apply_clarification_selections(
        {"source": "sql", "destination": "salesforce", "destination_name": "SF Prod"},
        plan,
        [{"clarification_id": "c-dest", "option_id": "destination:User"}],
    )
    assert out["destination_object_api_name"] == "User"
    assert out.get("planner_destination_entity_confirmed") is True
    assert "plan_contract" not in updated.metadata


def test_apply_pick_destination_connector():
    plan = ExecutionPlan(
        plan_id="p-dest-conn",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-dest-type",
                clarification_type="pick_destination_connector",
                prompt="Where?",
                options=[
                    ClarificationOption(
                        id="dest_type_salesforce",
                        label="Salesforce",
                        resource_ref=ResourceRef(
                            connector_type="salesforce",
                            display_name="SF Prod",
                        ),
                    ),
                ],
            ),
        ],
    )
    out, _updated = apply_clarification_selections(
        {"source": "sql", "source_name": "Test DB", "destination": "unknown"},
        plan,
        [{"clarification_id": "c-dest-type", "option_id": "dest_type_salesforce"}],
    )
    assert out["destination"] == "salesforce"
    assert out["destination_name"] == "SF Prod"


def test_pick_source_connector_type_does_not_mark_endpoint_clarified():
    plan = ExecutionPlan(
        plan_id="p-src-type",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-src-type",
                clarification_type="pick_source_connector",
                prompt="Where from?",
                options=[
                    ClarificationOption(
                        id="src_type_sql",
                        label="SQL database",
                        resource_ref=ResourceRef(
                            connector_type="sql",
                            display_name="SQL database",
                        ),
                    ),
                ],
            ),
        ],
    )
    out, _updated = apply_clarification_selections(
        {"source": "unknown", "destination": "salesforce", "destination_name": "SF 1"},
        plan,
        [{"clarification_id": "c-src-type", "option_id": "src_type_sql"}],
    )
    assert out["source"] == "sql"
    assert out.get("_source_endpoint_clarified") is None
    assert not out.get("source_name")


def test_pick_destination_connector_type_does_not_mark_endpoint_clarified():
    plan = ExecutionPlan(
        plan_id="p-dest-type",
        status="awaiting_clarification",
        clarifications=[
            Clarification(
                id="c-dest-type",
                clarification_type="pick_destination_connector",
                prompt="Where to?",
                options=[
                    ClarificationOption(
                        id="dest_type_salesforce",
                        label="Salesforce org",
                        resource_ref=ResourceRef(
                            connector_type="salesforce",
                            display_name="Salesforce org",
                        ),
                    ),
                ],
            ),
        ],
    )
    out, _updated = apply_clarification_selections(
        {"source": "sql", "source_name": "Test DB", "destination": "unknown"},
        plan,
        [{"clarification_id": "c-dest-type", "option_id": "dest_type_salesforce"}],
    )
    assert out["destination"] == "salesforce"
    assert out.get("_destination_endpoint_clarified") is None
    assert not out.get("destination_name")
