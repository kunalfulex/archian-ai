from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.connection import database
from services.pipeline_learning.embedding import embed_text, vector_to_pg_literal


def _jsonb_param(value: Optional[Any]) -> Optional[str]:
    """Serialize values for asyncpg JSONB columns (dicts must be JSON strings)."""
    if value is None:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return "{}"


class PipelineLearningStore:
    _initialized = False
    _init_lock = asyncio.Lock()

    @classmethod
    async def ensure_tables(cls) -> None:
        if cls._initialized:
            return
        async with cls._init_lock:
            if cls._initialized:
                return
            try:
                await database.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except Exception:
                pass
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    conversation_id UUID,
                    source_type VARCHAR(50),
                    destination_type VARCHAR(50),
                    source_fingerprint VARCHAR(64),
                    latest_job_id VARCHAR,
                    latest_mapping_message_id UUID,
                    latest_migration_batch_id VARCHAR,
                    status VARCHAR(32) NOT NULL DEFAULT 'active',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS stage_corrections (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    pipeline_run_id UUID NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    stage VARCHAR(32) NOT NULL,
                    stage_version INTEGER NOT NULL DEFAULT 1,
                    ai_snapshot JSONB,
                    final_snapshot JSONB,
                    diff JSONB,
                    context JSONB,
                    outcome JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_chunks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    pipeline_run_id UUID REFERENCES pipeline_runs(id) ON DELETE CASCADE,
                    stage VARCHAR(32) NOT NULL,
                    chunk_type VARCHAR(64) NOT NULL,
                    content_text TEXT NOT NULL,
                    embedding vector(1536),
                    metadata JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_retrieval_log (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    conversation_id UUID,
                    caller VARCHAR(64) NOT NULL,
                    query_text TEXT,
                    stage VARCHAR(32),
                    chunk_type VARCHAR(64),
                    source_type VARCHAR(50),
                    destination_type VARCHAR(50),
                    result_count INT NOT NULL DEFAULT 0,
                    chunk_ids JSONB NOT NULL DEFAULT '[]',
                    scores JSONB NOT NULL DEFAULT '[]',
                    chunk_types JSONB NOT NULL DEFAULT '[]',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_learning_retrieval_log_created
                ON learning_retrieval_log(created_at DESC)
                """
            )
            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_learning_retrieval_log_caller
                ON learning_retrieval_log(caller, created_at DESC)
                """
            )
            cls._initialized = True

    @classmethod
    async def get_or_create_run(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        source_fingerprint: Optional[str] = None,
    ) -> str:
        await cls.ensure_tables()
        uid = str(user_id)
        if conversation_id:
            row = await database.fetch_one(
                """
                SELECT id FROM pipeline_runs
                WHERE user_id = :user_id AND conversation_id = :conversation_id
                  AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                values={"user_id": uid, "conversation_id": str(conversation_id)},
            )
            if row:
                run_id = str(row["id"])
                await database.execute(
                    """
                    UPDATE pipeline_runs SET
                        source_type = COALESCE(:source_type, source_type),
                        destination_type = COALESCE(:destination_type, destination_type),
                        source_fingerprint = COALESCE(:source_fingerprint, source_fingerprint),
                        updated_at = NOW()
                    WHERE id = :id
                    """,
                    values={
                        "id": run_id,
                        "source_type": source_type,
                        "destination_type": destination_type,
                        "source_fingerprint": source_fingerprint,
                    },
                )
                return run_id

        run_id = str(uuid.uuid4())
        await database.execute(
            """
            INSERT INTO pipeline_runs (
                id, user_id, conversation_id, source_type, destination_type,
                source_fingerprint, status
            ) VALUES (
                :id, :user_id, :conversation_id, :source_type, :destination_type,
                :source_fingerprint, 'active'
            )
            """,
            values={
                "id": run_id,
                "user_id": uid,
                "conversation_id": str(conversation_id) if conversation_id else None,
                "source_type": source_type,
                "destination_type": destination_type,
                "source_fingerprint": source_fingerprint,
            },
        )
        return run_id

    @classmethod
    async def update_run(
        cls,
        run_id: str,
        *,
        latest_job_id: Optional[str] = None,
        latest_mapping_message_id: Optional[str] = None,
        latest_migration_batch_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        sets = ["updated_at = NOW()"]
        values: Dict[str, Any] = {"id": run_id}
        if latest_job_id is not None:
            sets.append("latest_job_id = :latest_job_id")
            values["latest_job_id"] = latest_job_id
        if latest_mapping_message_id is not None:
            sets.append("latest_mapping_message_id = :latest_mapping_message_id")
            values["latest_mapping_message_id"] = latest_mapping_message_id
        if latest_migration_batch_id is not None:
            sets.append("latest_migration_batch_id = :latest_migration_batch_id")
            values["latest_migration_batch_id"] = latest_migration_batch_id
        if status is not None:
            sets.append("status = :status")
            values["status"] = status
        await database.execute(
            f"UPDATE pipeline_runs SET {', '.join(sets)} WHERE id = :id",
            values=values,
        )

    @classmethod
    async def insert_correction(
        cls,
        *,
        pipeline_run_id: str,
        user_id: str,
        stage: str,
        stage_version: int,
        ai_snapshot: Optional[Dict[str, Any]],
        final_snapshot: Optional[Dict[str, Any]],
        diff: Optional[Dict[str, Any]],
        context: Optional[Dict[str, Any]],
        outcome: Optional[Dict[str, Any]] = None,
    ) -> str:
        await cls.ensure_tables()
        correction_id = str(uuid.uuid4())
        await database.execute(
            """
            INSERT INTO stage_corrections (
                id, pipeline_run_id, user_id, stage, stage_version,
                ai_snapshot, final_snapshot, diff, context, outcome
            ) VALUES (
                :id, :pipeline_run_id, :user_id, :stage, :stage_version,
                CAST(:ai_snapshot AS jsonb),
                CAST(:final_snapshot AS jsonb),
                CAST(:diff AS jsonb),
                CAST(:context AS jsonb),
                CAST(:outcome AS jsonb)
            )
            """,
            values={
                "id": correction_id,
                "pipeline_run_id": pipeline_run_id,
                "user_id": str(user_id),
                "stage": stage,
                "stage_version": stage_version,
                "ai_snapshot": _jsonb_param(ai_snapshot),
                "final_snapshot": _jsonb_param(final_snapshot),
                "diff": _jsonb_param(diff),
                "context": _jsonb_param(context),
                "outcome": _jsonb_param(outcome),
            },
        )
        return correction_id

    @classmethod
    async def upsert_chunk(
        cls,
        *,
        user_id: str,
        pipeline_run_id: Optional[str],
        stage: str,
        chunk_type: str,
        content_text: str,
        metadata: Dict[str, Any],
        embedding: Optional[List[float]] = None,
    ) -> str:
        await cls.ensure_tables()
        if not content_text.strip():
            return ""
        vec = embedding
        if vec is None:
            vec = await embed_text(content_text)
        chunk_id = str(uuid.uuid4())
        meta_json = json.dumps(metadata)
        if vec:
            vec_lit = vector_to_pg_literal(vec)
            await database.execute(
                """
                INSERT INTO learning_chunks (
                    id, user_id, pipeline_run_id, stage, chunk_type,
                    content_text, embedding, metadata
                ) VALUES (
                    :id, :user_id, :pipeline_run_id, :stage, :chunk_type,
                    :content_text, CAST(:embedding AS vector), CAST(:metadata AS jsonb)
                )
                """,
                values={
                    "id": chunk_id,
                    "user_id": str(user_id),
                    "pipeline_run_id": pipeline_run_id,
                    "stage": stage,
                    "chunk_type": chunk_type,
                    "content_text": content_text[:8000],
                    "embedding": vec_lit,
                    "metadata": meta_json,
                },
            )
        else:
            await database.execute(
                """
                INSERT INTO learning_chunks (
                    id, user_id, pipeline_run_id, stage, chunk_type,
                    content_text, metadata
                ) VALUES (
                    :id, :user_id, :pipeline_run_id, :stage, :chunk_type,
                    :content_text, CAST(:metadata AS jsonb)
                )
                """,
                values={
                    "id": chunk_id,
                    "user_id": str(user_id),
                    "pipeline_run_id": pipeline_run_id,
                    "stage": stage,
                    "chunk_type": chunk_type,
                    "content_text": content_text[:8000],
                    "metadata": meta_json,
                },
            )
        return chunk_id

    @classmethod
    async def update_chunks_success_rate(
        cls,
        pipeline_run_id: str,
        success_rate: float,
    ) -> None:
        await cls.ensure_tables()
        # Use CAST(... AS double precision) — not :success_rate::float, which the driver
        # parses as bind params :success_rate and :float.
        rate_json = json.dumps(float(success_rate))
        await database.execute(
            """
            UPDATE learning_chunks
            SET metadata = jsonb_set(
                    COALESCE(metadata, '{}'::jsonb),
                    '{success_rate}',
                    CAST(:success_rate_json AS jsonb)
                ),
                updated_at = NOW()
            WHERE pipeline_run_id = :pipeline_run_id
            """,
            values={
                "pipeline_run_id": pipeline_run_id,
                "success_rate_json": rate_json,
            },
        )

    @classmethod
    async def count_user_chunks(cls, user_id: str) -> int:
        await cls.ensure_tables()
        val = await database.fetch_val(
            "SELECT COUNT(*) FROM learning_chunks WHERE user_id = :user_id",
            values={"user_id": str(user_id)},
        )
        return int(val or 0)

    @classmethod
    async def log_retrieval(
        cls,
        *,
        user_id: str,
        caller: str,
        query_text: Optional[str],
        chunks: List[Dict[str, Any]],
        conversation_id: Optional[str] = None,
        stage: Optional[str] = None,
        chunk_type: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
    ) -> None:
        """Persist one retrieval event for super-admin learning metrics."""
        await cls.ensure_tables()
        chunk_ids: List[str] = []
        scores: List[float] = []
        chunk_types: List[str] = []
        for ch in chunks or []:
            cid = ch.get("id")
            if cid:
                chunk_ids.append(str(cid))
            score = ch.get("score")
            if score is None:
                score = ch.get("similarity")
            if score is not None:
                try:
                    scores.append(float(score))
                except (TypeError, ValueError):
                    pass
            ct = ch.get("chunk_type")
            if ct:
                chunk_types.append(str(ct))

        await database.execute(
            """
            INSERT INTO learning_retrieval_log (
                id, user_id, conversation_id, caller, query_text,
                stage, chunk_type, source_type, destination_type,
                result_count, chunk_ids, scores, chunk_types
            ) VALUES (
                :id, :user_id, :conversation_id, :caller, :query_text,
                :stage, :chunk_type, :source_type, :destination_type,
                :result_count,
                CAST(:chunk_ids AS jsonb),
                CAST(:scores AS jsonb),
                CAST(:chunk_types AS jsonb)
            )
            """,
            values={
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "conversation_id": str(conversation_id) if conversation_id else None,
                "caller": (caller or "unknown")[:64],
                "query_text": (query_text or "")[:4000] or None,
                "stage": stage,
                "chunk_type": chunk_type,
                "source_type": source_type,
                "destination_type": destination_type,
                "result_count": len(chunks or []),
                "chunk_ids": json.dumps(chunk_ids),
                "scores": json.dumps(scores),
                "chunk_types": json.dumps(chunk_types),
            },
        )
