"""Dispatch migration starts by destination (HTTP + agent)."""
from __future__ import annotations

from typing import Optional

from agent.session import AgentSession
from services.migration.context import MigrationContext, MigrationStartResult
from services.migration.errors import MigrationServiceError
from services.migration.gsheets_runner import start_google_sheets_migration
from services.migration.salesforce_runner import start_salesforce_migration
from services.migration.session_migration import resolve_migration_context_for_migrate
from services.migration.sql_runner import start_sql_migration


class MigrationService:
    @staticmethod
    async def start(
        ctx: MigrationContext,
        *,
        session: Optional[AgentSession] = None,
    ) -> MigrationStartResult:
        ctx = await resolve_migration_context_for_migrate(ctx, session=session)
        dest = (ctx.destination or "salesforce").lower()
        if dest == "gsheets":
            dest = "google_sheets"
        if dest == "salesforce":
            return await start_salesforce_migration(ctx)
        if dest in ("google_sheets", "gsheets"):
            return await start_google_sheets_migration(ctx)
        if dest == "sql":
            return await start_sql_migration(ctx)
        raise MigrationServiceError(
            f"Unsupported migration destination: {dest}",
            status_code=400,
        )
