from agent.session import AgentSession
from services.planning.plan_runner import _arguments_for_tool, refresh_step_arguments


def test_migrate_prefers_plan_destination_over_stale_snapshot():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={"destination_name": "contact list"},
    )
    raw = {
        "parameters": {
            "tool_id": "start_migration",
            "destination_name": "CRM contact list",
        }
    }
    args = _arguments_for_tool(
        "start_migration",
        session,
        raw,
        dict(raw["parameters"]),
    )
    assert args["destination_name"] == "CRM contact list"


def test_refresh_step_arguments_uses_session_after_create():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="extract",
        active_job_id="job-abc",
        destination="google_sheets",
        destination_name="CRM contact list",
    )
    args = refresh_step_arguments(
        "start_migration",
        {"destination_name": "contact list", "job_id": "old"},
        session,
    )
    assert args["job_id"] == "job-abc"
    assert args["destination_name"] == "contact list"


def test_refresh_map_fields_prefers_approved_snapshot_over_stale_session():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="map",
        active_job_id="job-abc",
        destination="salesforce",
        destination_name="SF 1",
        classification_snapshot={
            "destination": "salesforce",
            "destination_name": "SF 2",
            "destination_connector_id": "tok-2",
            "planner_destination_confirmed": True,
            "destination_object_api_name": "Contact",
        },
    )
    args = refresh_step_arguments(
        "map_fields",
        {
            "destination_name": "SF 2",
            "destination": "salesforce",
            "job_id": "job-abc",
        },
        session,
    )
    assert args["destination_name"] == "SF 2"
    assert args["destination_connector_id"] == "tok-2"


def test_refresh_create_step_pins_title_and_headers_from_snapshot():
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="create",
        classification_snapshot={
            "planner_new_spreadsheet_name": "My Friends",
            "planner_spreadsheet_headers": ["First name", "Last name", "Email"],
            "plan_scope": {
                "operations": ["create"],
                "spreadsheet_title": "My Friends",
            },
        },
    )
    args = refresh_step_arguments(
        "google_sheets_create_from_prompt",
        {"spreadsheet_title": "stale title"},
        session,
    )
    assert args["spreadsheet_title"] == "My Friends"
    assert args["headers"] == ["First name", "Last name", "Email"]


def test_refresh_extract_salesforce_rebuilds_contract_without_name_error():
    mi = {
        "driving_entity_key": "Contact",
        "field_hints": ["FirstName", "LastName", "Email"],
        "field_hints_strict": True,
        "extract_field_scope": "explicit",
        "extract_fields_confirmed": True,
        "include_related_data": True,
        "parent_entity_key": "Account",
        "lookup_relationship_name": "Account",
        "lookup_field_path": "Contact.AccountId",
        "related_field_hints": ["name", "phone"],
    }
    session = AgentSession(
        user_id="u1",
        conversation_id="c1",
        mode="extract",
        classification_snapshot={
            "source": "salesforce",
            "source_name": "SF 2",
            "related_entity_keys": ["Account"],
            "plan_contract": {
                "source": {
                    "entity_key": "Contact",
                    "entity_label": "Contact",
                    "resolution_source": "llm_bind",
                    "confidence": 0.95,
                    "connector_type": "salesforce",
                },
                "migration_intent": mi,
            },
            "migration_intent": mi,
        },
    )
    args = refresh_step_arguments(
        "extract_salesforce",
        {
            "destination": "unknown",
            "source_name": "SF 2",
            "source_entity_key": "Contact",
            "related_entity_keys": ["Account"],
        },
        session,
    )
    contract = args.get("extract_contract") or {}
    allowed = contract.get("allowed_select_fields") or []
    assert "Account.Name" in allowed
    assert "Account.Phone" in allowed
