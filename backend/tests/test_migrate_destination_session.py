"""Destination endpoint confirm after mapped job context."""
from services.planning.connector_endpoint_confirm import (
    destination_endpoint_is_confirmed,
    sanitize_unconfirmed_connector_endpoints_catalog,
)
from services.planning.continuation_policy import apply_job_destination_pins
from services.planning.resource_catalog import ResourceCatalog, SalesforceConnectionRef


def test_migrate_continuation_keeps_sf_destination_from_job_pins():
    cat = ResourceCatalog(
        salesforce_connections=[
            SalesforceConnectionRef(name="SF 1"),
            SalesforceConnectionRef(name="SF 2"),
        ]
    )
    from services.intent.job_context import JobContext, ResourcePin

    job = JobContext(
        job_id="job-1",
        pipeline_stage="mapped",
        mapping_ready=True,
        destination=ResourcePin(
            connector_type="salesforce",
            display_name="SF 1",
            entity_key="Contact",
        ),
    )
    cls = {
        "active_job_id": "job-1",
        "last_completed_step": "map",
        "intent": "migrate_data",
        "mode": "migrate",
        "destination": "salesforce",
    }
    cls = apply_job_destination_pins(cls, job)
    assert cls["destination_name"] == "SF 1"
    assert cls["planner_destination_confirmed"] is True

    sanitized = sanitize_unconfirmed_connector_endpoints_catalog(cls, cat)
    assert sanitized.get("destination_name") == "SF 1"
    assert destination_endpoint_is_confirmed(sanitized, "start the migration", cat)
