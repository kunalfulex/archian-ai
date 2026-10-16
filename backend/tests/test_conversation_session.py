from services.planning.continuation_policy import apply_extraction_session_continuation
from services.planning.conversation_session import (
    coerce_destination_type,
    latest_extraction_session_for_classifier,
)
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef


def test_coerce_destination_type_string_and_dict():
    assert coerce_destination_type("salesforce") == "salesforce"
    assert coerce_destination_type("gsheets") == "google_sheets"
    assert coerce_destination_type(None) == "salesforce"
    assert coerce_destination_type(
        {"connector_type": "salesforce", "display_name": "SF 2"}
    ) == "salesforce"
    assert coerce_destination_type({"type": "sql", "name": "My DB"}) == "sql"


def test_latest_extraction_session_from_history():
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "job_id": "job-1",
                "source_type": "salesforce",
                "destination": "google_sheets",
                "source_name": "Prod",
                "destination_name": "Export",
            }
        }
    ]
    sess = latest_extraction_session_for_classifier(history)
    assert sess["job_id"] == "job-1"
    assert sess["active_source"] == "salesforce"


def test_apply_latest_uses_catalog_resolver():
    from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef

    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="Sandbox", environment_type="sandbox"),
            SalesforceConnectionRef(name="Production", environment_type="production"),
        ]
    )
    history = [
        {
            "metadata": {
                "event_type": "extraction_completed",
                "source_type": "salesforce",
                "destination": "salesforce",
                "source_name": "Production",
                "destination_name": "Production",
                "job_id": "j1",
            }
        }
    ]
    classification = {
        "intent": "map_data",
        "mode": "map",
        "source": "salesforce",
        "destination": "salesforce",
        "destination_name": "Sandbox",
    }
    out = apply_extraction_session_continuation(
        {**classification, "session_action": "continue_active_job"},
        extraction_meta=history[0]["metadata"],
        prompt="map to Sandbox",
        catalog=cat,
        conversation_history=history,
    )
    assert out["destination_name"] == "Sandbox"
