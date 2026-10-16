"""Tests for canonical DB-first migration context resolution."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from services.migration.context import MigrationContext
from services.migration.session_migration import resolve_migration_context_for_migrate


def test_resolve_migrate_prefers_db_mapping_over_http_body():
    http_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Email": {"column": "Email", "sheet": "Sheet1"}},
        document_type="Contact",
        destination="google_sheets",
        conversation_id="conv-1",
        spreadsheet_id="sheet-abc",
        sheet_name="Sheet1",
    )
    db_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={
            "Email": {"column": "Email", "sheet": "Sheet1"},
            "MobilePhone": {"column": "Address", "sheet": "Sheet1"},
        },
        document_type="Contact",
        destination="google_sheets",
        conversation_id="conv-1",
        mapping_version=3,
        transformation_rules={"fields": [{"action": "combine"}]},
    )

    async def run():
        with patch(
            "services.migration.session_migration._load_canonical_migration_context",
            new_callable=AsyncMock,
            return_value=db_ctx,
        ):
            merged = await resolve_migration_context_for_migrate(http_ctx)
        assert "MobilePhone" in merged.mapping
        assert merged.mapping_version == 3
        assert merged.spreadsheet_id == "sheet-abc"
        assert merged.transformation_rules == db_ctx.transformation_rules

    asyncio.run(run())


def test_resolve_migrate_falls_back_to_http_when_no_db():
    http_ctx = MigrationContext(
        user_id="user-1",
        extraction_job_id="job-1",
        mapping={"Phone": {"column": "Phone", "sheet": "Sheet1"}},
        document_type="Contact",
        destination="google_sheets",
        conversation_id="conv-1",
    )

    async def run():
        with patch(
            "services.migration.session_migration._load_canonical_migration_context",
            new_callable=AsyncMock,
            return_value=None,
        ):
            merged = await resolve_migration_context_for_migrate(http_ctx)
        assert merged.mapping == http_ctx.mapping

    asyncio.run(run())
