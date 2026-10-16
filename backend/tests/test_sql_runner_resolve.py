import asyncio

import pytest
from unittest.mock import AsyncMock, patch

from services.migration.context import MigrationContext
from services.migration.errors import MigrationServiceError
from services.migration.sql_runner import start_sql_migration


def test_start_sql_resolves_connector_from_extraction_row():
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"email": {"table": "users", "column": "email"}},
        document_type="User Management",
        destination="sql",
        destination_name="Test DB",
    )

    async def run():
        with patch(
            "services.migration.sql_runner.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "sql",
                    "source_identifier": "conn-from-row",
                }
            ),
        ), patch(
            "services.migration.sql_runner.validate_extraction_for_migration",
            new=AsyncMock(return_value=4),
        ), patch(
            "services.migration.sql_runner.validate_plan_for_migration",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.sql_runner.check_migration_credits",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.sql_runner.MigrationRecordsService.create_migration_batch",
            new=AsyncMock(return_value="batch-1"),
        ), patch(
            "services.migration.sql_runner.database_background_migration_service.start_background_migration",
            new=AsyncMock(return_value="mig-job-1"),
        ):
            result = await start_sql_migration(ctx)
        assert result.job_id == "mig-job-1"
        assert ctx.connector_id == "conn-from-row"
        assert ctx.table_name == "users"

    asyncio.run(run())


def test_start_sql_still_requires_connector_when_unresolvable():
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"email": {"column": "email"}},
        document_type="User Management",
        destination="sql",
        destination_name="Unknown DB",
    )

    async def run():
        with patch(
            "services.migration.sql_runner.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration._lookup_sql_connector_id",
            new=AsyncMock(return_value=None),
        ):
            with pytest.raises(MigrationServiceError) as exc:
                await start_sql_migration(ctx)
            assert "connector ID" in exc.value.message

    asyncio.run(run())
