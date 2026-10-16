"""Start migration from session / migration context."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services.migration.errors import MigrationServiceError
from services.migration.migration_service import MigrationService


async def run_migrate_action(
    *,
    user_id: str,
    conversation_id: str,
    job_id: Optional[str] = None,
    mapping_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    from services.conversation.session_store import load_or_hydrate_session
    from services.migration.session_migration import resolve_migration_context_for_execution

    session_snap = await load_or_hydrate_session(user_id, conversation_id)
    resolved_job_id = job_id or session_snap.active_job_id
    if not resolved_job_id:
        raise ValueError("No active extraction job for migration.")

    ctx = await resolve_migration_context_for_execution(
        user_id=user_id,
        conversation_id=conversation_id,
        job_id=str(resolved_job_id),
        mapping_message_id=mapping_message_id or session_snap.latest_mapping_message_id,
    )
    if not ctx or not ctx.mapping:
        raise ValueError("Complete field mapping before starting migration.")

    ctx.conversation_id = conversation_id
    if mapping_message_id:
        ctx.mapping_id = ctx.mapping_id or mapping_message_id

    try:
        result = await MigrationService.start(ctx)
    except MigrationServiceError as exc:
        if exc.payload:
            return {"status": "error", **exc.payload}
        raise

    from services.conversation.session_sync import sync_session_after_migration

    await sync_session_after_migration(user_id, conversation_id, job_id=str(resolved_job_id))

    return {
        "status": "success",
        "job_id": result.job_id,
        "batch_id": result.batch_id,
        "destination": result.destination,
        "destination_name": result.destination_name,
        "total_records": result.total_records,
        "source_job_id": result.source_job_id,
        "conversation_id": conversation_id,
    }
