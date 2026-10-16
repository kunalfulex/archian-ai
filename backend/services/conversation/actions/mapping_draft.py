"""Mapping draft session storage + debounced chat metadata flush."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from db.connection import database
from services.chat_service import ChatService
from services.conversation.session_store import load_or_hydrate_session, update_session
from services.migration.context import MigrationContext
from services.migration.context_repository import get_context, patch_context, upsert_context
from services.migration.write_policy import resolve_write_policy

logger = logging.getLogger(__name__)


async def patch_mapping_draft(
    user_id: str,
    conversation_id: str,
    draft: Dict[str, Any],
) -> Dict[str, Any]:
    session = await update_session(
        user_id,
        conversation_id,
        mapping_draft=dict(draft),
    )
    result: Dict[str, Any] = {"status": "success", "mapping_draft": session.mapping_draft}

    target_job_id = (draft.get("job_id") or session.active_job_id or "").strip()
    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    if target_job_id and isinstance(mapping_cfg, dict) and mapping_cfg:
        message_id = session.latest_mapping_message_id
        meta = None
        if message_id:
            meta = await _load_mapping_message_metadata(user_id, conversation_id, message_id)
        saved = await _persist_draft_to_db_context(
            user_id=user_id,
            conversation_id=conversation_id,
            draft=draft,
            target_job_id=target_job_id,
            conv_session=session,
            meta=meta,
            message_id=message_id,
        )
        if saved:
            result["context_persisted"] = True
            result["context_job_id"] = saved.extraction_job_id
            result["mapping_version"] = saved.mapping_version

    return result


async def _load_mapping_message_metadata(
    user_id: str,
    conversation_id: str,
    message_id: str,
) -> Optional[Dict[str, Any]]:
    row = await database.fetch_one(
        """
        SELECT metadata
        FROM chat_messages
        WHERE id = :message_id
          AND conversation_id = :conversation_id
          AND user_id = :user_id
        """,
        {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
        },
    )
    if not row:
        return None
    raw = row["metadata"]
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
    return dict(raw) if isinstance(raw, dict) else None


def _normalize_destination(dest: Optional[str]) -> str:
    d = (dest or "salesforce").strip().lower()
    if d == "gsheets":
        return "google_sheets"
    return d or "salesforce"


def _migration_rules_from_draft(draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rules: Dict[str, Any] = {}
    pref = draft.get("missing_field_preference")
    if pref is not None:
        rules["missing_field_preference"] = pref
    return rules or None


def _meta_from_migration_context(
    ctx: MigrationContext,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Fill chat/draft metadata gaps from a stored migration context row."""
    out = dict(meta or {})
    if ctx.destination and not out.get("destination"):
        out["destination"] = ctx.destination
    if ctx.destination_name and not out.get("destination_name"):
        out["destination_name"] = ctx.destination_name
    if ctx.document_type and not out.get("document_type"):
        out["document_type"] = ctx.document_type
        out.setdefault("destination_document_type", ctx.document_type)
    if ctx.destination_object_api_name and not out.get("destination_object_api_name"):
        out["destination_object_api_name"] = ctx.destination_object_api_name
    if ctx.table_name and not out.get("table_name"):
        out["table_name"] = ctx.table_name
        out.setdefault("primary_table", ctx.table_name)
    if ctx.connector_id and not out.get("destination_connector_id"):
        out["destination_connector_id"] = ctx.connector_id
    if ctx.source_connector_id and not out.get("source_connector_id"):
        out["source_connector_id"] = ctx.source_connector_id
    if ctx.mapping_id and not out.get("mapping_id"):
        out["mapping_id"] = ctx.mapping_id
    return out


async def _persist_draft_to_db_context(
    *,
    user_id: str,
    conversation_id: str,
    draft: Dict[str, Any],
    target_job_id: str,
    conv_session,
    meta: Optional[Dict[str, Any]] = None,
    message_id: Optional[str] = None,
) -> Optional[MigrationContext]:
    """
    Persist mapping draft to conversation_migration_contexts.

    Tries the target job first, then the lineage owner that holds the canonical mapping.
    """
    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    if not isinstance(mapping_cfg, dict) or not mapping_cfg:
        return None

    if meta is None and message_id:
        meta = await _load_mapping_message_metadata(user_id, conversation_id, message_id)
    meta = dict(meta or {})

    for key in ("destination", "destination_name", "table_name"):
        if draft.get(key) and not meta.get(key):
            meta[key] = draft[key]
    if draft.get("table_name"):
        meta.setdefault("primary_table", draft["table_name"])

    saved = await _upsert_context_from_draft(
        user_id=user_id,
        conversation_id=conversation_id,
        job_id=str(target_job_id),
        draft=draft,
        conv_session=conv_session,
        meta=meta,
    )
    if saved:
        return saved

    from services.migration.mapping_lineage import find_lineage_mapping_context

    found = await find_lineage_mapping_context(
        user_id=str(user_id),
        conversation_id=str(conversation_id),
        target_job_id=str(target_job_id),
    )
    if not found:
        return None

    ancestor_ctx, owner_job_id = found
    enriched_meta = _meta_from_migration_context(ancestor_ctx, meta)
    saved = await _upsert_context_from_draft(
        user_id=user_id,
        conversation_id=conversation_id,
        job_id=str(owner_job_id),
        draft=draft,
        conv_session=conv_session,
        meta=enriched_meta,
    )
    if saved:
        logger.info(
            "persist_draft_to_db_context: applied draft via lineage owner "
            "conversation_id=%s target_job=%s owner_job=%s",
            conversation_id,
            target_job_id,
            owner_job_id,
        )
    return saved


async def _upsert_context_from_draft(
    *,
    user_id: str,
    conversation_id: str,
    job_id: str,
    draft: Dict[str, Any],
    conv_session,
    meta: Optional[Dict[str, Any]],
) -> Optional[MigrationContext]:
    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    if not isinstance(mapping_cfg, dict) or not mapping_cfg:
        return None

    intent = conv_session.intent_state if conv_session else {}
    meta = meta or {}
    destination = _normalize_destination(
        draft.get("destination")
        or meta.get("destination")
        or (intent or {}).get("active_destination")
    )
    destination_name = (
        draft.get("destination_name")
        or meta.get("destination_name")
        or (intent or {}).get("destination_name")
    )
    document_type = str(
        meta.get("destination_document_type")
        or meta.get("document_type")
        or ""
    )
    destination_object_api_name = (
        draft.get("destination_object_api_name")
        or meta.get("destination_object_api_name")
    )
    mapping_id = meta.get("mapping_id") or getattr(conv_session, "latest_mapping_message_id", None)
    table_name = (
        draft.get("table_name")
        or draft.get("destination_object_api_name")
        or meta.get("primary_table")
        or meta.get("table_name")
    )
    transformation_rules = draft.get("transformation_rules")
    migration_rules = _migration_rules_from_draft(draft)

    existing = await get_context(
        user_id=user_id,
        conversation_id=conversation_id,
        extraction_job_id=job_id,
    )
    if existing:
        write_policy = resolve_write_policy(
            destination=existing.destination or destination,
            mapping=mapping_cfg,
        )
        updated = await patch_context(
            user_id=user_id,
            conversation_id=conversation_id,
            extraction_job_id=job_id,
            mapping=dict(mapping_cfg),
            transformation_rules=transformation_rules,
            table_name=table_name or existing.table_name,
            migration_rules=migration_rules,
        )
        if updated and write_policy.operation != updated.operation:
            updated.operation = write_policy.operation
            updated = await upsert_context(updated)
        if updated and destination_object_api_name:
            from utils.migration_destination_entity import coerce_destination_entity_api_name

            resolved = coerce_destination_entity_api_name(
                destination_object_api_name,
                connector_type=updated.destination or destination,
                mapping=mapping_cfg,
            )
            if resolved and resolved != updated.destination_object_api_name:
                updated.destination_object_api_name = resolved
                updated = await upsert_context(updated)
        return updated

    if not document_type and not destination_name:
        logger.warning(
            "flush_mapping_draft_to_context: skip upsert — no DB row and insufficient metadata "
            "conversation_id=%s job_id=%s",
            conversation_id,
            job_id,
        )
        return None

    write_policy = resolve_write_policy(destination=destination, mapping=mapping_cfg)
    from utils.migration_destination_entity import coerce_destination_entity_api_name

    resolved_entity = coerce_destination_entity_api_name(
        destination_object_api_name,
        connector_type=destination,
        mapping=mapping_cfg,
    )
    ctx = MigrationContext(
        user_id=user_id,
        extraction_job_id=job_id,
        mapping=dict(mapping_cfg),
        document_type=document_type,
        destination_object_api_name=resolved_entity,
        destination=destination,
        destination_name=destination_name,
        document_description=meta.get("destination_document_description")
        or meta.get("document_description"),
        conversation_id=conversation_id,
        mapping_id=str(mapping_id) if mapping_id else None,
        transformation_rules=transformation_rules,
        migration_rules=migration_rules,
        connector_id=meta.get("destination_connector_id") or meta.get("connector_id"),
        source_connector_id=meta.get("source_connector_id") or meta.get("connector_id"),
        table_name=str(table_name) if table_name else None,
        spreadsheet_id=meta.get("destination_spreadsheet_id") or meta.get("spreadsheet_id"),
        source_type=meta.get("source_type"),
        sql_query=meta.get("sql_query"),
        operation=write_policy.operation,
    )
    return await upsert_context(ctx)


async def flush_mapping_draft_to_chat(
    user_id: str,
    conversation_id: str,
    *,
    job_id: Optional[str] = None,
    mapping_message_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Persist mapping draft to chat_messages.metadata at commit points."""
    session = await load_or_hydrate_session(user_id, conversation_id)
    draft = session.mapping_draft
    message_id = mapping_message_id or session.latest_mapping_message_id
    if not draft or not message_id:
        return None

    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    patch: Dict[str, Any] = {}
    if isinstance(mapping_cfg, dict):
        patch["mapping_config"] = mapping_cfg
        patch["field_mappings_count"] = len(mapping_cfg)
    for key in (
        "transformation_rules",
        "missing_field_preference",
        "mapping_instruction",
        "destination_object_api_name",
    ):
        if draft.get(key) is not None:
            patch[key] = draft[key]
    if job_id:
        patch["job_id"] = job_id

    if not patch:
        return None

    return await ChatService.update_message_metadata(
        message_id,
        conversation_id,
        user_id,
        patch,
    )


async def flush_mapping_draft_to_context(
    user_id: str,
    conversation_id: str,
    *,
    job_id: Optional[str] = None,
    mapping_message_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Commit mapping draft to chat metadata and canonical conversation_migration_contexts.
    """
    session = await load_or_hydrate_session(user_id, conversation_id)
    draft = session.mapping_draft
    resolved_job_id = job_id or session.active_job_id
    message_id = mapping_message_id or session.latest_mapping_message_id
    if not draft or not resolved_job_id:
        return None

    mapping_cfg = draft.get("mapping_config") or draft.get("mapping")
    if not isinstance(mapping_cfg, dict) or not mapping_cfg:
        return None

    chat_updated = await flush_mapping_draft_to_chat(
        user_id,
        conversation_id,
        job_id=str(resolved_job_id),
        mapping_message_id=message_id,
    )

    meta = None
    if message_id:
        meta = await _load_mapping_message_metadata(user_id, conversation_id, message_id)
        if chat_updated and isinstance(chat_updated.get("metadata"), dict):
            meta = {**(meta or {}), **chat_updated["metadata"]}

    saved = await _persist_draft_to_db_context(
        user_id=user_id,
        conversation_id=conversation_id,
        draft=draft,
        target_job_id=str(resolved_job_id),
        conv_session=session,
        meta=meta,
        message_id=message_id,
    )

    result: Dict[str, Any] = {
        "job_id": str(resolved_job_id),
        "mapping_field_count": len(mapping_cfg),
        "chat_updated": chat_updated is not None,
    }
    if saved:
        result["mapping_version"] = saved.mapping_version
        result["mapping_id"] = saved.mapping_id
        result["context_job_id"] = saved.extraction_job_id
    return result
