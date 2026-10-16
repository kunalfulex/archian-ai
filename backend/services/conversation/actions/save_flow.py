"""Save mapping flow from session state."""
from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from db.extracted_schema import save_mapping_flow


async def run_save_flow_action(
    *,
    user_id: str,
    conversation_id: str,
    flow_name: str,
    job_id: Optional[str] = None,
    mapping_message_id: Optional[str] = None,
    batch_id: Optional[str] = None,
) -> Dict[str, Any]:
    from services.conversation.session_store import load_or_hydrate_session, update_session
    from services.conversation.actions.mapping_draft import flush_mapping_draft_to_context
    from services.flow.save_context import (
        resolve_flow_save_context,
        resolve_save_flow_records_job_id,
    )

    session_snap = await load_or_hydrate_session(user_id, conversation_id)
    resolved_job_id = await resolve_save_flow_records_job_id(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        job_id=job_id,
        batch_id=batch_id,
        session_active_job_id=session_snap.active_job_id,
    )

    await update_session(
        str(user_id),
        str(conversation_id),
        active_job_id=str(resolved_job_id),
    )

    await flush_mapping_draft_to_context(
        user_id,
        conversation_id,
        job_id=str(resolved_job_id),
        mapping_message_id=mapping_message_id or session_snap.latest_mapping_message_id,
    )

    session_after_flush = await load_or_hydrate_session(user_id, conversation_id)

    save_ctx = await resolve_flow_save_context(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        records_job_id=str(resolved_job_id),
        batch_id=batch_id,
        mapping_draft=session_after_flush.mapping_draft,
    )
    payload = save_ctx.to_save_request(flow_name.strip())
    result = await save_mapping_flow(payload, UUID(str(user_id)))
    flow_id = result.get("flow_id") if isinstance(result, dict) else None

    try:
        from services.chat_service import ChatService

        await ChatService.log_data_event(
            conversation_id=str(conversation_id),
            user_id=str(user_id),
            event_type="flow_created",
            event_data={
                "flow_id": flow_id,
                "flow_name": flow_name.strip(),
                "source_type": save_ctx.extracted_schema_type,
                "source_name": save_ctx.source_name or save_ctx.document_type,
                "destination_type": save_ctx.destination,
                "destination_name": save_ctx.destination_name,
                "job_id": save_ctx.records_job_id,
                "batch_id": batch_id,
            },
        )
    except Exception:
        pass

    return {"status": "success", "flow_id": flow_id, **(result if isinstance(result, dict) else {"result": result})}
