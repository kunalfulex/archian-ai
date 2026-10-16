"""Tests for migration_completed save-flow metadata builder."""
import asyncio

from services.migration.completed_event_metadata import (
    build_migration_completed_event_data,
    resolve_destination_connector_from_job,
)


def test_build_migration_completed_includes_destination_connector():
    job_id = "mig-job-1"
    job_data = {
        "batch_id": "batch-1",
        "destination": "salesforce",
        "destination_name": "SF 1",
        "salesforce_token_id": "token-uuid",
        "source_job_id": "ext-job-1",
        "successful_records": 0,
        "failed_records": 2,
        "status": "completed",
        "instance_url": "https://example.my.salesforce.com",
    }

    async def _run():
        return await build_migration_completed_event_data(job_id, job_data, "user-1")

    event = asyncio.run(_run())
    assert event["destination_connector_id"] == "token-uuid"
    assert event["source_job_id"] == "ext-job-1"
    assert event["job_id"] == job_id


def test_resolve_destination_connector_gsheets():
    assert (
        resolve_destination_connector_from_job(
            {"destination_type": "google_sheets", "spreadsheet_id": "sheet-abc"}
        )
        == "sheet-abc"
    )


def test_resolve_destination_connector_sql():
    assert (
        resolve_destination_connector_from_job(
            {"destination": "sql", "connector_id": "conn-42"}
        )
        == "conn-42"
    )
