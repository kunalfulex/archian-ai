from services.mapping_plan.target_resolution import resolve_mapping_target


def test_classification_snapshot_preferred_over_prompt_heuristics():
    """Planner/classifier object wins over regex prompt parsing."""
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction="map to SF 1 org Service Appointment object",
        classification_snapshot={"destination_object_api_name": "Contact"},
    )
    assert resolved.connector_type == "salesforce"
    assert resolved.target_object_api_name == "Contact"
    assert resolved.resolution_source == "classification_explicit"


def test_salesforce_snapshot_object_used_when_prompt_missing():
    resolved = resolve_mapping_target(
        destination="salesforce",
        mapping_instruction="start mapping to SF 1",
        classification_snapshot={"destination_object_api_name": "Contact"},
    )
    assert resolved.target_object_api_name == "Contact"
    assert resolved.resolution_source == "classification_explicit"


def test_sql_prompt_target_table_resolution():
    resolved = resolve_mapping_target(
        destination="sql",
        mapping_instruction="map into table service_appointments",
        classification_snapshot={},
    )
    assert resolved.connector_type == "sql"
    assert resolved.target_table == "service_appointments"
    assert resolved.resolution_source == "prompt_explicit"


def test_google_sheets_prompt_target_sheet_resolution():
    resolved = resolve_mapping_target(
        destination="google_sheets",
        mapping_instruction="map to sheet Sheet2",
        classification_snapshot={},
    )
    assert resolved.connector_type == "google_sheets"
    assert resolved.target_sheet == "Sheet2"
    assert resolved.resolution_source == "prompt_explicit"

