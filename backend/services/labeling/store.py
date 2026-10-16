"""Persistence for entity semantics and labeling jobs."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Dict, List, Optional

from db.connection import database
from services.labeling.connector_ref import connector_instance_id


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


class LabelingStore:
    _initialized = False
    _init_lock = asyncio.Lock()

    @classmethod
    async def ensure_tables(cls) -> None:
        if cls._initialized:
            return
        async with cls._init_lock:
            if cls._initialized:
                return
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_entity_semantics (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    connector_type VARCHAR(32) NOT NULL,
                    connector_instance_id VARCHAR(128) NOT NULL,
                    connector_ref JSONB NOT NULL DEFAULT '{}',
                    entity_key VARCHAR(512) NOT NULL,
                    entity_display_name VARCHAR(512),
                    semantics JSONB,
                    labeling_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    schema_hash VARCHAR(64),
                    semantics_version INTEGER NOT NULL DEFAULT 1,
                    labeled_at TIMESTAMPTZ,
                    error_summary TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_entity_semantics_unique
                ON connector_entity_semantics(user_id, connector_type, connector_instance_id, entity_key)
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_labeling_jobs (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    connector_type VARCHAR(32) NOT NULL,
                    connector_ref JSONB NOT NULL DEFAULT '{}',
                    connector_instance_id VARCHAR(128) NOT NULL,
                    job_type VARCHAR(32) NOT NULL DEFAULT 'entity_batch',
                    entity_keys JSONB NOT NULL DEFAULT '[]',
                    status VARCHAR(16) NOT NULL DEFAULT 'queued',
                    progress JSONB NOT NULL DEFAULT '{}',
                    error_summary TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_connector_labeling_jobs_active
                ON connector_labeling_jobs(user_id, connector_type, connector_instance_id, status)
                WHERE status IN ('queued', 'running')
                """
            )
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS connector_business_profiles (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    connector_type VARCHAR(32) NOT NULL,
                    connector_instance_id VARCHAR(128) NOT NULL,
                    connector_ref JSONB NOT NULL DEFAULT '{}',
                    display_name VARCHAR(512),
                    user_description TEXT,
                    ai_profile JSONB,
                    profile_status VARCHAR(16) NOT NULL DEFAULT 'pending',
                    profile_version INTEGER NOT NULL DEFAULT 1,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await database.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_connector_business_profiles_unique
                ON connector_business_profiles(user_id, connector_type, connector_instance_id)
                """
            )
            await database.execute(
                """
                ALTER TABLE connector_business_profiles
                ADD COLUMN IF NOT EXISTS metadata_sync_completed_at TIMESTAMPTZ
                """
            )
            await database.execute(
                """
                ALTER TABLE connector_business_profiles
                ADD COLUMN IF NOT EXISTS agent_ready_notified_at TIMESTAMPTZ
                """
            )
            await database.execute(
                """
                ALTER TABLE connector_business_profiles
                ADD COLUMN IF NOT EXISTS readiness_phase VARCHAR(32) DEFAULT 'pending'
                """
            )
            await database.execute(
                """
                ALTER TABLE connector_business_profiles
                ADD COLUMN IF NOT EXISTS metadata_sync_started_at TIMESTAMPTZ
                """
            )
            await database.execute(
                """
                ALTER TABLE connector_business_profiles
                ADD COLUMN IF NOT EXISTS metadata_sync_progress JSONB NOT NULL DEFAULT '{}'
                """
            )
            cls._initialized = True
            await cls._migrate_salesforce_connector_keys()
            await cls._ensure_label_evidence_table()

    @classmethod
    async def _ensure_label_evidence_table(cls) -> None:
        await database.execute(
            """
            CREATE TABLE IF NOT EXISTS label_evidence (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type VARCHAR(32) NOT NULL,
                pipeline_run_id UUID,
                conversation_id VARCHAR(128),
                connector_type VARCHAR(32),
                connector_instance_id VARCHAR(128),
                connector_ref JSONB NOT NULL DEFAULT '{}',
                entity_key VARCHAR(512),
                entity_display_name VARCHAR(512),
                prompt_excerpt TEXT,
                user_phrase TEXT,
                context JSONB NOT NULL DEFAULT '{}',
                outcome JSONB,
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        await database.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_label_evidence_pending
            ON label_evidence(user_id, connector_type, connector_instance_id, created_at DESC)
            WHERE processed = FALSE
            """
        )

    @classmethod
    async def _migrate_salesforce_connector_keys(cls) -> None:
        """Re-key labeling rows from org_id to salesforce_tokens.id (idempotent)."""
        token_pick = """
            SELECT DISTINCT ON (user_id, org_id)
                user_id, id::text AS connector_id, org_id::text AS org_id
            FROM salesforce_tokens
            ORDER BY user_id, org_id, issued_at DESC
        """
        for table in (
            "connector_entity_semantics",
            "connector_labeling_jobs",
            "connector_business_profiles",
        ):
            await database.execute(
                f"""
                UPDATE {table} t
                SET connector_instance_id = tok.connector_id,
                    connector_ref = jsonb_build_object(
                        'connector_id', tok.connector_id,
                        'org_id', tok.org_id
                    )
                FROM ({token_pick}) tok
                WHERE t.connector_type = 'salesforce'
                  AND t.user_id = tok.user_id
                  AND t.connector_instance_id = tok.org_id
                  AND t.connector_instance_id <> tok.connector_id
                """
            )

    @classmethod
    async def get_active_job(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        job_type: str = "entity_batch",
    ) -> Optional[Dict[str, Any]]:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        row = await database.fetch_one(
            """
            SELECT * FROM connector_labeling_jobs
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
              AND job_type = :job_type
              AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "job_type": job_type,
            },
        )
        return dict(row) if row else None

    @classmethod
    async def create_job(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_keys: List[str],
        job_type: str = "entity_batch",
    ) -> str:
        await cls.ensure_tables()
        job_id = str(uuid.uuid4())
        inst = connector_instance_id(connector_type, connector_ref)
        await database.execute(
            """
            INSERT INTO connector_labeling_jobs (
                id, user_id, connector_type, connector_ref, connector_instance_id,
                job_type, entity_keys, status, progress
            ) VALUES (
                :id, :user_id, :connector_type, CAST(:connector_ref AS jsonb),
                :connector_instance_id, :job_type, CAST(:entity_keys AS jsonb),
                'queued', CAST(:progress AS jsonb)
            )
            """,
            {
                "id": job_id,
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_ref": _json_dumps(connector_ref),
                "connector_instance_id": inst,
                "job_type": job_type,
                "entity_keys": _json_dumps(entity_keys),
                "progress": _json_dumps({"labeled": 0, "total": len(entity_keys), "message": "Queued"}),
            },
        )
        return job_id

    @classmethod
    async def merge_entity_keys_into_active_job(
        cls,
        job_id: str,
        user_id: str,
        extra_keys: List[str],
    ) -> None:
        if not extra_keys:
            return
        row = await cls.get_job(job_id, user_id)
        if not row:
            return
        existing = row.get("entity_keys") or []
        if isinstance(existing, str):
            existing = json.loads(existing)
        merged = list(dict.fromkeys([*(existing or []), *extra_keys]))
        await cls.update_job(
            job_id,
            user_id,
            entity_keys=merged,
            progress={
                **(row.get("progress") if isinstance(row.get("progress"), dict) else {}),
                "total": len(merged),
            },
        )

    @classmethod
    async def get_job(cls, job_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        await cls.ensure_tables()
        row = await database.fetch_one(
            """
            SELECT * FROM connector_labeling_jobs
            WHERE id = :id AND user_id = :user_id
            """,
            {"id": job_id, "user_id": str(user_id)},
        )
        if not row:
            return None
        out = dict(row)
        for key in ("connector_ref", "entity_keys", "progress"):
            val = out.get(key)
            if isinstance(val, str):
                try:
                    out[key] = json.loads(val)
                except json.JSONDecodeError:
                    pass
        return out

    @classmethod
    async def update_job(cls, job_id: str, user_id: str, **fields: Any) -> None:
        await cls.ensure_tables()
        sets = ["updated_at = NOW()"]
        values: Dict[str, Any] = {"id": job_id, "user_id": str(user_id)}
        json_cols = {"connector_ref", "entity_keys", "progress"}
        for key, val in fields.items():
            if key in json_cols:
                sets.append(f"{key} = CAST(:{key} AS jsonb)")
                values[key] = _json_dumps(val)
            else:
                sets.append(f"{key} = :{key}")
                values[key] = val
        await database.execute(
            f"""
            UPDATE connector_labeling_jobs SET {', '.join(sets)}
            WHERE id = :id AND user_id = :user_id
            """,
            values,
        )

    @classmethod
    async def upsert_entity_pending(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
        entity_display_name: Optional[str] = None,
        schema_hash: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await database.execute(
            """
            INSERT INTO connector_entity_semantics (
                id, user_id, connector_type, connector_instance_id, connector_ref,
                entity_key, entity_display_name, labeling_status, schema_hash
            ) VALUES (
                :id, :user_id, :connector_type, :connector_instance_id,
                CAST(:connector_ref AS jsonb), :entity_key, :entity_display_name,
                'pending', :schema_hash
            )
            ON CONFLICT (user_id, connector_type, connector_instance_id, entity_key)
            DO UPDATE SET
                entity_display_name = COALESCE(EXCLUDED.entity_display_name, connector_entity_semantics.entity_display_name),
                schema_hash = COALESCE(EXCLUDED.schema_hash, connector_entity_semantics.schema_hash),
                labeling_status = CASE
                    WHEN connector_entity_semantics.labeling_status = 'ready'
                         AND connector_entity_semantics.schema_hash IS NOT DISTINCT FROM EXCLUDED.schema_hash
                    THEN 'ready'
                    ELSE 'pending'
                END,
                updated_at = NOW()
            """,
            {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "connector_ref": _json_dumps(connector_ref),
                "entity_key": entity_key,
                "entity_display_name": entity_display_name,
                "schema_hash": schema_hash,
            },
        )

    @classmethod
    async def save_entity_semantics(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
        entity_display_name: Optional[str],
        semantics: Dict[str, Any],
        schema_hash: Optional[str],
        labeling_status: str = "ready",
        error_summary: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await database.execute(
            """
            INSERT INTO connector_entity_semantics (
                id, user_id, connector_type, connector_instance_id, connector_ref,
                entity_key, entity_display_name, semantics, labeling_status, schema_hash,
                labeled_at, error_summary, semantics_version
            ) VALUES (
                :id, :user_id, :connector_type, :connector_instance_id,
                CAST(:connector_ref AS jsonb), :entity_key, :entity_display_name,
                CAST(:semantics AS jsonb), CAST(:labeling_status AS VARCHAR(16)), CAST(:schema_hash AS VARCHAR(64)),
                CASE WHEN CAST(:labeling_status AS VARCHAR(16)) = 'ready' THEN NOW() ELSE NULL END,
                CAST(:error_summary AS TEXT), 1
            )
            ON CONFLICT (user_id, connector_type, connector_instance_id, entity_key)
            DO UPDATE SET
                entity_display_name = COALESCE(EXCLUDED.entity_display_name, connector_entity_semantics.entity_display_name),
                semantics = EXCLUDED.semantics,
                labeling_status = EXCLUDED.labeling_status,
                schema_hash = COALESCE(EXCLUDED.schema_hash, connector_entity_semantics.schema_hash),
                labeled_at = CASE WHEN EXCLUDED.labeling_status = 'ready' THEN NOW() ELSE connector_entity_semantics.labeled_at END,
                error_summary = EXCLUDED.error_summary,
                semantics_version = connector_entity_semantics.semantics_version + 1,
                updated_at = NOW()
            """,
            {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "connector_ref": _json_dumps(connector_ref),
                "entity_key": entity_key,
                "entity_display_name": entity_display_name,
                "semantics": _json_dumps(semantics),
                "labeling_status": labeling_status,
                "schema_hash": schema_hash,
                "error_summary": error_summary,
            },
        )

    @classmethod
    async def get_entity_labeling_stats(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
    ) -> Dict[str, int]:
        await cls.ensure_tables()
        row = await database.fetch_one(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE labeling_status = 'ready')::int AS ready,
                COUNT(*) FILTER (WHERE labeling_status = 'failed')::int AS failed,
                COUNT(*) FILTER (WHERE labeling_status = 'pending')::int AS pending,
                COUNT(*) FILTER (WHERE labeling_status = 'processing')::int AS processing
            FROM connector_entity_semantics
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
            },
        )
        if not row:
            return {"total": 0, "ready": 0, "failed": 0, "pending": 0, "processing": 0}
        r = dict(row)
        total = int(r.get("total") or 0)
        ready = int(r.get("ready") or 0)
        failed = int(r.get("failed") or 0)
        pending = int(r.get("pending") or 0)
        processing = int(r.get("processing") or 0)
        return {
            "total": total,
            "ready": ready,
            "failed": failed,
            "pending": pending,
            "processing": processing,
        }

    @classmethod
    async def get_user_labeling_metrics(cls, user_id: str) -> Dict[str, Any]:
        await cls.ensure_tables()
        entity_row = await database.fetch_one(
            """
            SELECT
                COUNT(*)::int AS total,
                COUNT(*) FILTER (WHERE labeling_status = 'ready')::int AS ready,
                COUNT(*) FILTER (WHERE labeling_status = 'failed')::int AS failed,
                COUNT(*) FILTER (WHERE labeling_status = 'pending')::int AS pending,
                COUNT(*) FILTER (WHERE labeling_status = 'processing')::int AS processing
            FROM connector_entity_semantics
            WHERE user_id = :user_id
            """,
            {"user_id": str(user_id)},
        )
        job_row = await database.fetch_one(
            """
            SELECT
                COUNT(*) FILTER (WHERE status IN ('queued', 'running'))::int AS active,
                COUNT(*) FILTER (WHERE status = 'failed')::int AS failed,
                COUNT(*) FILTER (WHERE status = 'completed')::int AS completed
            FROM connector_labeling_jobs
            WHERE user_id = :user_id
            """,
            {"user_id": str(user_id)},
        )
        e = dict(entity_row) if entity_row else {}
        j = dict(job_row) if job_row else {}
        return {
            "entities": {
                "total": int(e.get("total") or 0),
                "ready": int(e.get("ready") or 0),
                "failed": int(e.get("failed") or 0),
                "pending": int(e.get("pending") or 0),
                "processing": int(e.get("processing") or 0),
            },
            "jobs": {
                "active": int(j.get("active") or 0),
                "failed": int(j.get("failed") or 0),
                "completed": int(j.get("completed") or 0),
            },
        }

    @classmethod
    async def list_active_jobs(
        cls,
        user_id: str,
        *,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        await cls.ensure_tables()
        rows = await database.fetch_all(
            """
            SELECT id, connector_type, connector_instance_id, job_type, status,
                   progress, error_summary, created_at, updated_at
            FROM connector_labeling_jobs
            WHERE user_id = :user_id
              AND status IN ('queued', 'running')
            ORDER BY created_at DESC
            LIMIT :limit
            """,
            {"user_id": str(user_id), "limit": limit},
        )
        out: List[Dict[str, Any]] = []
        for row in rows or []:
            item = dict(row)
            for key in ("progress",):
                val = item.get(key)
                if isinstance(val, str):
                    try:
                        item[key] = json.loads(val)
                    except json.JSONDecodeError:
                        pass
            out.append(item)
        return out

    @classmethod
    async def list_failed_entities(
        cls,
        user_id: str,
        *,
        connector_type: Optional[str] = None,
        connector_instance_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        await cls.ensure_tables()
        clauses = ["user_id = :user_id", "labeling_status = 'failed'"]
        values: Dict[str, Any] = {"user_id": str(user_id), "limit": limit}
        if connector_type:
            clauses.append("connector_type = :connector_type")
            values["connector_type"] = connector_type
        if connector_instance_id:
            clauses.append("connector_instance_id = :connector_instance_id")
            values["connector_instance_id"] = connector_instance_id
        rows = await database.fetch_all(
            f"""
            SELECT connector_type, connector_instance_id, entity_key,
                   entity_display_name, error_summary, schema_hash, updated_at
            FROM connector_entity_semantics
            WHERE {' AND '.join(clauses)}
            ORDER BY updated_at DESC
            LIMIT :limit
            """,
            values,
        )
        return [dict(r) for r in rows or []]

    @classmethod
    async def find_stale_entity_keys(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        current_hashes: Dict[str, str],
    ) -> List[str]:
        """Entities whose stored hash differs from current schema or need (re)labeling."""
        await cls.ensure_tables()
        rows = await database.fetch_all(
            """
            SELECT entity_key, schema_hash, labeling_status
            FROM connector_entity_semantics
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
            },
        )
        stale: List[str] = []
        known: set[str] = set()
        for row in rows or []:
            r = dict(row)
            key = r.get("entity_key")
            if not key:
                continue
            known.add(str(key))
            if str(key) not in current_hashes:
                continue
            stored_hash = r.get("schema_hash")
            current_hash = current_hashes.get(str(key))
            status = r.get("labeling_status")
            if stored_hash != current_hash:
                stale.append(str(key))
            elif status in ("pending", "failed", "processing"):
                stale.append(str(key))
        for key in current_hashes:
            if key not in known:
                stale.append(key)
        return list(dict.fromkeys(stale))

    @classmethod
    async def reset_failed_entities(
        cls,
        user_id: str,
        *,
        connector_type: Optional[str] = None,
        connector_instance_id: Optional[str] = None,
        entity_keys: Optional[List[str]] = None,
    ) -> int:
        await cls.ensure_tables()
        clauses = ["user_id = :user_id", "labeling_status = 'failed'"]
        values: Dict[str, Any] = {"user_id": str(user_id)}
        if connector_type:
            clauses.append("connector_type = :connector_type")
            values["connector_type"] = connector_type
        if connector_instance_id:
            clauses.append("connector_instance_id = :connector_instance_id")
            values["connector_instance_id"] = connector_instance_id
        if entity_keys:
            placeholders = ", ".join(f":ek{i}" for i in range(len(entity_keys)))
            clauses.append(f"entity_key IN ({placeholders})")
            for i, key in enumerate(entity_keys):
                values[f"ek{i}"] = key
        result = await database.fetch_all(
            f"""
            UPDATE connector_entity_semantics
            SET labeling_status = 'pending',
                error_summary = NULL,
                updated_at = NOW()
            WHERE {' AND '.join(clauses)}
            RETURNING entity_key
            """,
            values,
        )
        return len(result or [])

    @classmethod
    async def list_ready_entity_summaries(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        await cls.ensure_tables()
        rows = await database.fetch_all(
            """
            SELECT entity_key, entity_display_name, semantics
            FROM connector_entity_semantics
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
              AND labeling_status = 'ready'
              AND semantics IS NOT NULL
            ORDER BY labeled_at DESC NULLS LAST
            LIMIT :limit
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "limit": limit,
            },
        )
        out: List[Dict[str, Any]] = []
        for row in rows or []:
            r = dict(row)
            sem = r.get("semantics")
            if isinstance(sem, str):
                try:
                    sem = json.loads(sem)
                except json.JSONDecodeError:
                    sem = {}
            out.append(
                {
                    "entity_key": r.get("entity_key"),
                    "entity_display_name": r.get("entity_display_name"),
                    "semantics": sem if isinstance(sem, dict) else {},
                }
            )
        return out

    @classmethod
    async def get_business_profile(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
    ) -> Optional[Dict[str, Any]]:
        await cls.ensure_tables()
        row = await database.fetch_one(
            """
            SELECT * FROM connector_business_profiles
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
            },
        )
        if not row:
            return None
        out = dict(row)
        for key in ("connector_ref", "ai_profile", "metadata_sync_progress"):
            val = out.get(key)
            if isinstance(val, str):
                try:
                    out[key] = json.loads(val)
                except json.JSONDecodeError:
                    pass
        return out

    @classmethod
    async def upsert_business_profile_shell(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        display_name: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await database.execute(
            """
            INSERT INTO connector_business_profiles (
                id, user_id, connector_type, connector_instance_id, connector_ref,
                display_name, profile_status
            ) VALUES (
                :id, :user_id, :connector_type, :connector_instance_id,
                CAST(:connector_ref AS jsonb), :display_name, 'pending'
            )
            ON CONFLICT (user_id, connector_type, connector_instance_id)
            DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, connector_business_profiles.display_name),
                connector_ref = EXCLUDED.connector_ref,
                updated_at = NOW()
            """,
            {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "connector_ref": _json_dumps(connector_ref),
                "display_name": display_name,
            },
        )

    @classmethod
    async def update_user_description(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        user_description: str,
    ) -> None:
        await cls.ensure_tables()
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET user_description = :user_description,
                profile_status = 'stale',
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "user_description": user_description,
            },
        )

    @classmethod
    async def save_ai_profile(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        display_name: Optional[str],
        ai_profile: Dict[str, Any],
        profile_status: str = "ready",
        user_description: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await database.execute(
            """
            INSERT INTO connector_business_profiles (
                id, user_id, connector_type, connector_instance_id, connector_ref,
                display_name, user_description, ai_profile, profile_status, profile_version
            ) VALUES (
                :id, :user_id, :connector_type, :connector_instance_id,
                CAST(:connector_ref AS jsonb), :display_name, :user_description,
                CAST(:ai_profile AS jsonb), :profile_status, 1
            )
            ON CONFLICT (user_id, connector_type, connector_instance_id)
            DO UPDATE SET
                display_name = COALESCE(EXCLUDED.display_name, connector_business_profiles.display_name),
                ai_profile = EXCLUDED.ai_profile,
                profile_status = EXCLUDED.profile_status,
                user_description = COALESCE(connector_business_profiles.user_description, EXCLUDED.user_description),
                profile_version = connector_business_profiles.profile_version + 1,
                updated_at = NOW()
            """,
            {
                "id": str(uuid.uuid4()),
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "connector_ref": _json_dumps(connector_ref),
                "display_name": display_name,
                "user_description": user_description,
                "ai_profile": _json_dumps(ai_profile),
                "profile_status": profile_status,
            },
        )

    @classmethod
    async def insert_label_evidence(
        cls,
        *,
        user_id: str,
        event_type: str,
        connector_type: Optional[str] = None,
        connector_ref: Optional[Dict[str, Any]] = None,
        entity_key: Optional[str] = None,
        entity_display_name: Optional[str] = None,
        prompt_excerpt: Optional[str] = None,
        user_phrase: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        outcome: Optional[Dict[str, Any]] = None,
        pipeline_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        await cls.ensure_tables()
        evidence_id = str(uuid.uuid4())
        connector_ref = dict(connector_ref or {})
        inst = (
            connector_instance_id(connector_type, connector_ref)
            if connector_type and connector_ref
            else None
        )
        await database.execute(
            """
            INSERT INTO label_evidence (
                id, user_id, event_type, pipeline_run_id, conversation_id,
                connector_type, connector_instance_id, connector_ref,
                entity_key, entity_display_name, prompt_excerpt, user_phrase,
                context, outcome
            ) VALUES (
                :id, :user_id, :event_type, :pipeline_run_id, :conversation_id,
                :connector_type, :connector_instance_id, CAST(:connector_ref AS jsonb),
                :entity_key, :entity_display_name, :prompt_excerpt, :user_phrase,
                CAST(:context AS jsonb), CAST(:outcome AS jsonb)
            )
            """,
            {
                "id": evidence_id,
                "user_id": str(user_id),
                "event_type": event_type,
                "pipeline_run_id": pipeline_run_id,
                "conversation_id": conversation_id,
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "connector_ref": _json_dumps(connector_ref),
                "entity_key": entity_key,
                "entity_display_name": entity_display_name,
                "prompt_excerpt": (prompt_excerpt or "")[:2000] or None,
                "user_phrase": (user_phrase or "")[:2000] or None,
                "context": _json_dumps(context or {}),
                "outcome": _json_dumps(outcome) if outcome is not None else None,
            },
        )
        return evidence_id

    @classmethod
    async def list_pending_evidence(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        await cls.ensure_tables()
        rows = await database.fetch_all(
            """
            SELECT * FROM label_evidence
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
              AND processed = FALSE
            ORDER BY created_at ASC
            LIMIT :limit
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "limit": limit,
            },
        )
        out: List[Dict[str, Any]] = []
        for row in rows or []:
            r = dict(row)
            for key in ("connector_ref", "context", "outcome"):
                val = r.get(key)
                if isinstance(val, str):
                    try:
                        r[key] = json.loads(val)
                    except json.JSONDecodeError:
                        pass
            out.append(r)
        return out

    @classmethod
    async def mark_evidence_processed(cls, evidence_ids: List[str], user_id: str) -> None:
        if not evidence_ids:
            return
        await cls.ensure_tables()
        for evidence_id in evidence_ids:
            await database.execute(
                """
                UPDATE label_evidence
                SET processed = TRUE
                WHERE user_id = :user_id AND id = :id
                """,
                {"user_id": str(user_id), "id": str(evidence_id)},
            )

    @classmethod
    async def get_entity_semantics_row(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        entity_key: str,
    ) -> Optional[Dict[str, Any]]:
        await cls.ensure_tables()
        row = await database.fetch_one(
            """
            SELECT entity_key, entity_display_name, semantics, labeling_status, schema_hash
            FROM connector_entity_semantics
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
              AND entity_key = :entity_key
              AND labeling_status = 'ready'
            LIMIT 1
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "entity_key": entity_key,
            },
        )
        if not row:
            return None
        out = dict(row)
        sem = out.get("semantics")
        if isinstance(sem, str):
            try:
                out["semantics"] = json.loads(sem)
            except json.JSONDecodeError:
                out["semantics"] = {}
        return out

    @classmethod
    async def merge_entity_semantics(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        entity_key: str,
        merged_semantics: Dict[str, Any],
    ) -> None:
        row = await cls.get_entity_semantics_row(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=connector_instance_id(connector_type, connector_ref),
            entity_key=entity_key,
        )
        if not row:
            return
        await cls.save_entity_semantics(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            entity_display_name=row.get("entity_display_name"),
            semantics=merged_semantics,
            schema_hash=row.get("schema_hash"),
            labeling_status="ready",
        )
        from services.labeling.legacy_sync import sync_primary_to_legacy_columns
        from services.labeling.models import EntitySemantics

        await sync_primary_to_legacy_columns(
            user_id=user_id,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            semantics=EntitySemantics.from_llm_dict(merged_semantics),
        )

    @classmethod
    async def merge_connector_profile_synonyms(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        add_synonyms: List[str],
        add_business_domains: List[str],
    ) -> None:
        profile = await cls.get_business_profile(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=connector_instance_id,
        )
        if not profile:
            return
        ai = dict(profile.get("ai_profile") or {})
        existing_syn = [str(x) for x in (ai.get("synonyms") or []) if str(x).strip()]
        existing_dom = [str(x) for x in (ai.get("business_domains") or []) if str(x).strip()]
        merged_syn = _dedupe_strings(existing_syn + add_synonyms)
        merged_dom = _dedupe_strings(existing_dom + add_business_domains)
        if merged_syn == existing_syn and merged_dom == existing_dom:
            return
        ai["synonyms"] = merged_syn
        ai["business_domains"] = merged_dom
        ai["source"] = "execution_learning"
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET ai_profile = CAST(:ai_profile AS jsonb),
                profile_status = 'ready',
                profile_version = profile_version + 1,
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "ai_profile": _json_dumps(ai),
            },
        )

    @classmethod
    async def mark_metadata_sync_started(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        display_name: Optional[str] = None,
    ) -> None:
        """Mark schema/metadata sync in flight (readiness stays on metadata_sync until complete)."""
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await cls.upsert_business_profile_shell(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
            display_name=display_name,
        )
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET metadata_sync_started_at = NOW(),
                readiness_phase = 'metadata_sync',
                metadata_sync_progress = CAST(:progress AS jsonb),
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
                "progress": _json_dumps({"processed": 0, "total": 0}),
            },
        )

    @classmethod
    async def update_metadata_sync_progress(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        processed: Optional[int] = None,
        total: Optional[int] = None,
        percent: Optional[int] = None,
        message: Optional[str] = None,
    ) -> None:
        """Update live schema/metadata sync progress for readiness banner."""
        await cls.ensure_tables()
        profile = await cls.get_business_profile(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_instance_id=str(connector_instance_id),
        )
        current = profile.get("metadata_sync_progress") if profile else {}
        if not isinstance(current, dict):
            current = {}
        progress = dict(current)
        if processed is not None:
            progress["processed"] = int(processed)
        if total is not None:
            progress["total"] = int(total)
        if percent is not None:
            progress["percent"] = int(percent)
        if message is not None:
            progress["message"] = message
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET metadata_sync_progress = CAST(:progress AS jsonb),
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "progress": _json_dumps(progress),
            },
        )

    @classmethod
    async def clear_metadata_sync_in_progress(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
    ) -> None:
        await cls.ensure_tables()
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET metadata_sync_started_at = NULL,
                metadata_sync_progress = CAST('{}' AS jsonb),
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
            },
        )

    @classmethod
    async def mark_metadata_sync_complete(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        display_name: Optional[str] = None,
    ) -> None:
        await cls.ensure_tables()
        inst = connector_instance_id(connector_type, connector_ref)
        await cls.upsert_business_profile_shell(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
            display_name=display_name,
        )
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET metadata_sync_completed_at = NOW(),
                metadata_sync_started_at = NULL,
                metadata_sync_progress = CAST('{}' AS jsonb),
                readiness_phase = CASE
                    WHEN readiness_phase = 'ready' THEN readiness_phase
                    ELSE 'entity_labeling'
                END,
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": inst,
            },
        )

    @classmethod
    async def update_readiness_phase(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
        phase: str,
    ) -> None:
        await cls.ensure_tables()
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET readiness_phase = CAST(:phase AS VARCHAR(32)),
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
                "phase": phase,
            },
        )

    @classmethod
    async def mark_agent_ready_notified(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_instance_id: str,
    ) -> None:
        await cls.ensure_tables()
        await database.execute(
            """
            UPDATE connector_business_profiles
            SET agent_ready_notified_at = NOW(),
                readiness_phase = 'ready',
                updated_at = NOW()
            WHERE user_id = :user_id
              AND connector_type = :connector_type
              AND connector_instance_id = :connector_instance_id
            """,
            {
                "user_id": str(user_id),
                "connector_type": connector_type,
                "connector_instance_id": str(connector_instance_id),
            },
        )


def _dedupe_strings(values: List[str], *, max_items: int = 40) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for raw in values:
        val = str(raw).strip()
        if not val:
            continue
        key = val.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(val)
        if len(out) >= max_items:
            break
    return out
