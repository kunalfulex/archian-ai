"""Gate terminal extraction_job_finished SSE at end of data_ops turns."""
from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.session import AgentSession

MIGRATION_TERMINAL_ACTIONS = frozenset(
    {
        "migration_started",
        "migration_needs_mapping",
        "migration_blocked_by_audit",
    }
)

NON_EXTRACTION_TERMINAL_ACTIONS = frozenset(
    {
        "map_fields_ready",
        "mapping_completed",
        "mapping_needs_review",
    }
)


def is_migration_terminal_result(result: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(result, dict):
        return False
    action = (result.get("action") or "").strip().lower()
    if action in MIGRATION_TERMINAL_ACTIONS:
        return True
    if result.get("batch_id") and not result.get("operations_applied"):
        return True
    return False


def should_emit_terminal_extraction_finished(session: "AgentSession") -> bool:
    if session.mode != "data_ops":
        return False
    last_result = session.last_result
    if not isinstance(last_result, dict):
        return False
    if is_migration_terminal_result(last_result):
        return False
    action = (last_result.get("action") or "").strip().lower()
    if action in NON_EXTRACTION_TERMINAL_ACTIONS:
        return False
    if last_result.get("operations_applied"):
        return True
    if last_result.get("extraction_version") is not None:
        return True
    if last_result.get("job_id") and not action:
        return True
    return False


async def build_terminal_extraction_finished_sse(session: "AgentSession") -> Optional[str]:
    if not should_emit_terminal_extraction_finished(session):
        return None
    if not session.active_job_id or not session.last_result:
        return None
    from utils.extraction_sse_payload import build_job_status_after_db_save, extraction_job_finished_sse

    classification_meta = {
        "source": session.source,
        "destination": session.destination,
        "source_name": session.source_name,
        "destination_name": session.destination_name,
    }
    job_status = await build_job_status_after_db_save(
        job_id=session.active_job_id,
        user_id=session.user_id,
        ops_meta=dict(session.last_result),
        classification=classification_meta,
    )
    return extraction_job_finished_sse(session.active_job_id, job_status)
