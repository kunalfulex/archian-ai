import asyncio

import pytest

from services.migration.context import MigrationContext
from services.migration.errors import MigrationServiceError
from services.migration.migration_service import MigrationService


def test_start_salesforce_requires_mapping(monkeypatch):
    async def fake_start(ctx):
        assert ctx.mapping

    monkeypatch.setattr(
        "services.migration.migration_service.start_salesforce_migration",
        fake_start,
    )
    ctx = MigrationContext(
        user_id="u1",
        extraction_job_id="job-1",
        mapping={"Email": {"object": "Contact", "field": "Email"}},
        document_type="Contact",
        destination="salesforce",
    )
    asyncio.run(MigrationService.start(ctx))


def test_start_sql_dispatches(monkeypatch):
    async def fake_sql(ctx):
        assert ctx.destination == "sql"
        assert ctx.connector_id == "conn-1"

    monkeypatch.setattr(
        "services.migration.migration_service.start_sql_migration",
        fake_sql,
    )
    ctx = MigrationContext(
        user_id="u1",
        extraction_job_id="job-1",
        mapping={"col": {"column": "col"}},
        document_type="t",
        destination="sql",
        connector_id="conn-1",
        table_name="users",
    )
    asyncio.run(MigrationService.start(ctx))


def test_start_google_sheets_dispatches(monkeypatch):
    async def fake_gs(ctx):
        assert ctx.spreadsheet_id == "sheet-abc"

    monkeypatch.setattr(
        "services.migration.migration_service.start_google_sheets_migration",
        fake_gs,
    )
    ctx = MigrationContext(
        user_id="u1",
        extraction_job_id="job-1",
        mapping={"Name": {"column": "Name"}},
        document_type="t",
        destination="google_sheets",
        spreadsheet_id="sheet-abc",
        sheet_name="Sheet1",
    )
    asyncio.run(MigrationService.start(ctx))


def test_start_unsupported_destination():
    ctx = MigrationContext(
        user_id="u1",
        extraction_job_id="job-1",
        mapping={"x": {}},
        document_type="t",
        destination="hubspot",
    )

    async def run():
        with pytest.raises(MigrationServiceError) as exc:
            await MigrationService.start(ctx)
        assert exc.value.status_code == 400

    asyncio.run(run())
