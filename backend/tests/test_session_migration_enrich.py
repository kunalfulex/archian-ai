import asyncio
from unittest.mock import AsyncMock, patch

from agent.session import AgentSession
from services.migration.context import MigrationContext
from services.migration.session_migration import (
    _infer_sql_table_from_mapping,
    _merge_persisted_context,
    enrich_migration_context,
    resolve_migration_context_for_tool,
)


def test_infer_sql_table_from_mapping():
    mapping = {
        "email": {"table": "users", "column": "email"},
        "name": {"table": "users", "column": "name"},
        "plan": {"table": "user_plans", "column": "plan_id"},
    }
    assert _infer_sql_table_from_mapping(mapping) == "users"


def test_merge_persisted_context_fills_missing_fields():
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Name": {"column": "Name"}},
        document_type="Sheet",
        destination="google_sheets",
    )
    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={},
        document_type="Sheet",
        destination="google_sheets",
        spreadsheet_id="sheet-abc",
        sheet_name="Contacts",
    )
    _merge_persisted_context(ctx, db_ctx)
    assert ctx.spreadsheet_id == "sheet-abc"
    assert ctx.sheet_name == "Contacts"


def test_enrich_sql_from_extraction_source_identifier():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="extract",
        destination="sql",
        destination_name="Test DB",
        mapping_config={"email": {"table": "users", "column": "email"}},
    )
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
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "sql",
                    "source_identifier": "conn-uuid-123",
                }
            ),
        ), patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ):
            out = await enrich_migration_context(ctx, session, "job-1")
        assert out.connector_id == "conn-uuid-123"
        assert out.table_name == "users"

    asyncio.run(run())


def test_enrich_gsheets_from_extraction_source_identifier():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="extract",
        destination="google_sheets",
        destination_name="Contact List",
        mapping_config={"Name": {"column": "Name"}},
    )
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Name": {"column": "Name"}},
        document_type="Contacts",
        destination="google_sheets",
        destination_name="Contact List",
    )

    async def run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(
                return_value={
                    "source_type": "google_sheets",
                    "source_identifier": "spreadsheet-xyz",
                }
            ),
        ), patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration._lookup_gsheets_sheet_name",
            new=AsyncMock(return_value="Sheet1"),
        ):
            out = await enrich_migration_context(ctx, session, "job-1")
        assert out.spreadsheet_id == "spreadsheet-xyz"
        assert out.sheet_name == "Sheet1"

    asyncio.run(run())


def test_enrich_salesforce_token_from_last_result():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="extract",
        destination="salesforce",
        destination_name="Production Org",
        mapping_config={"Email": {"object": "Contact", "field": "Email"}},
        last_result={"destination_connector_id": "token-99"},
    )
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Email": {"object": "Contact", "field": "Email"}},
        document_type="Contact",
        destination="salesforce",
        destination_name="Production Org",
    )

    async def run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ):
            out = await enrich_migration_context(ctx, session, "job-1")
        assert out.connector_id == "token-99"

    asyncio.run(run())


def test_enrich_sql_from_destination_name_lookup():
    session = AgentSession(
        user_id="user-1",
        conversation_id=None,
        mode="extract",
        destination="sql",
        destination_name="Test DB",
        mapping_config={"id": {"table": "users", "column": "id"}},
    )
    ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"id": {"table": "users", "column": "id"}},
        document_type="User Management",
        destination="sql",
        destination_name="Test DB",
    )

    async def run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration._lookup_sql_connector_id",
            new=AsyncMock(return_value="conn-by-name"),
        ):
            out = await enrich_migration_context(ctx, session, "job-1")
        assert out.connector_id == "conn-by-name"

    asyncio.run(run())


def test_resolve_merges_persisted_context_when_session_has_mapping():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="extract",
        destination="google_sheets",
        destination_name="Contact List",
        mapping_config={"Name": {"column": "Name"}},
        last_result={},
    )
    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Name": {"column": "Name"}},
        document_type="Contacts",
        destination="google_sheets",
        spreadsheet_id="persisted-sheet-id",
        sheet_name="Data",
    )

    async def run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=db_ctx),
        ), patch(
            "services.migration.session_migration._lookup_gsheets_spreadsheet_id",
            new=AsyncMock(return_value=None),
        ):
            ctx = await resolve_migration_context_for_tool(session, "job-1")
        assert ctx is not None
        assert ctx.spreadsheet_id == "persisted-sheet-id"
        assert ctx.sheet_name == "Data"

    asyncio.run(run())


def test_resolve_uses_session_mapping_and_enriches_sql():
    session = AgentSession(
        user_id="user-1",
        conversation_id="conv-1",
        mode="extract",
        destination="sql",
        destination_name="Test DB",
        mapping_config={"email": {"table": "users", "column": "email"}},
        last_result={"destination_connector_id": "conn-from-map", "primary_table": "users"},
    )

    async def run():
        with patch(
            "services.extraction_results_service.ExtractionResultsService.get_extraction_result",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.session_migration.get_context",
            new=AsyncMock(return_value=None),
        ), patch(
            "services.migration.mapping_lineage.find_lineage_mapping_context",
            new=AsyncMock(return_value=None),
        ):
            ctx = await resolve_migration_context_for_tool(session, "job-1")
        assert ctx is not None
        assert ctx.connector_id == "conn-from-map"
        assert ctx.table_name == "users"

    asyncio.run(run())
