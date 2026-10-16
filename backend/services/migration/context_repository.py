"""Persist migration context for agent + API clients."""
from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional

from db.connection import database
from services.migration.context import MigrationContext
from services.migration.db_row import row_get

_SCHEMA_READY = False


async def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    await database.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_migration_contexts (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            conversation_id UUID NOT NULL,
            extraction_job_id UUID NOT NULL,
            mapping_id TEXT NOT NULL,
            mapping_version INT NOT NULL DEFAULT 1,
            destination TEXT NOT NULL,
            destination_name TEXT,
            mapping_config JSONB NOT NULL DEFAULT '{}'::jsonb,
            context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, conversation_id, extraction_job_id)
        )
        """
    )
    _SCHEMA_READY = True


async def upsert_context(ctx: MigrationContext) -> MigrationContext:
    await ensure_schema()
    if not ctx.conversation_id:
        return ctx
    mapping_id = ctx.mapping_id or f"mapping-{uuid.uuid4().hex[:12]}"
    storage = ctx.to_storage_dict()
    storage["mapping_id"] = mapping_id
    row = await database.fetch_one(
        """
        INSERT INTO conversation_migration_contexts (
            user_id, conversation_id, extraction_job_id,
            mapping_id, mapping_version, destination, destination_name,
            mapping_config, context_json
        )
        VALUES (
            :user_id, :conversation_id, :extraction_job_id,
            :mapping_id, :mapping_version, :destination, :destination_name,
            CAST(:mapping_config AS jsonb), CAST(:context_json AS jsonb)
        )
        ON CONFLICT (user_id, conversation_id, extraction_job_id)
        DO UPDATE SET
            mapping_id = EXCLUDED.mapping_id,
            mapping_version = conversation_migration_contexts.mapping_version + 1,
            destination = EXCLUDED.destination,
            destination_name = EXCLUDED.destination_name,
            mapping_config = EXCLUDED.mapping_config,
            context_json = EXCLUDED.context_json,
            updated_at = now()
        RETURNING mapping_version, mapping_id
        """,
        values={
            "user_id": ctx.user_id,
            "conversation_id": ctx.conversation_id,
            "extraction_job_id": ctx.extraction_job_id,
            "mapping_id": mapping_id,
            "mapping_version": ctx.mapping_version,
            "destination": ctx.destination,
            "destination_name": ctx.destination_name,
            "mapping_config": json.dumps(ctx.mapping),
            "context_json": json.dumps(storage),
        },
    )
    if row:
        ctx.mapping_version = int(row_get(row, "mapping_version") or ctx.mapping_version)
        ctx.mapping_id = row_get(row, "mapping_id") or ctx.mapping_id
    ctx.mapping_id = mapping_id
    return ctx


async def get_context(
    *,
    user_id: str,
    conversation_id: str,
    extraction_job_id: str,
) -> Optional[MigrationContext]:
    await ensure_schema()
    row = await database.fetch_one(
        """
        SELECT context_json, mapping_version, mapping_id
        FROM conversation_migration_contexts
        WHERE user_id = :user_id
          AND conversation_id = :conversation_id
          AND extraction_job_id = :extraction_job_id
        """,
        values={
            "user_id": user_id,
            "conversation_id": conversation_id,
            "extraction_job_id": extraction_job_id,
        },
    )
    if not row:
        return None
    raw = row["context_json"]
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = dict(raw or {})
    ctx = MigrationContext.from_storage_dict(data)
    ctx.mapping_version = int(row_get(row, "mapping_version") or ctx.mapping_version)
    ctx.mapping_id = row_get(row, "mapping_id") or ctx.mapping_id
    return ctx


async def patch_mapping(
    *,
    user_id: str,
    conversation_id: str,
    extraction_job_id: str,
    mapping: Dict[str, Any],
    expected_version: Optional[int] = None,
) -> Optional[MigrationContext]:
    return await patch_context(
        user_id=user_id,
        conversation_id=conversation_id,
        extraction_job_id=extraction_job_id,
        mapping=mapping,
        expected_version=expected_version,
    )


async def patch_context(
    *,
    user_id: str,
    conversation_id: str,
    extraction_job_id: str,
    mapping: Optional[Dict[str, Any]] = None,
    transformation_rules: Optional[Dict[str, Any]] = None,
    table_name: Optional[str] = None,
    migration_rules: Optional[Dict[str, Any]] = None,
    expected_version: Optional[int] = None,
) -> Optional[MigrationContext]:
    existing = await get_context(
        user_id=user_id,
        conversation_id=conversation_id,
        extraction_job_id=extraction_job_id,
    )
    if not existing:
        return None
    if expected_version is not None and existing.mapping_version != expected_version:
        return None
    if mapping is not None:
        existing.mapping = dict(mapping)
    if transformation_rules is not None:
        existing.transformation_rules = transformation_rules
    if table_name is not None:
        existing.table_name = table_name
    if migration_rules is not None:
        merged = dict(existing.migration_rules or {})
        merged.update(migration_rules)
        existing.migration_rules = merged
    return await upsert_context(existing)
