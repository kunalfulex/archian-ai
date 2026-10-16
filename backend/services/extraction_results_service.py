import asyncio
import csv
import io
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

from db.connection import database
from utils.extraction_record_display import DISPLAY_METADATA_KEYS, strip_row_display_metadata


class ExtractionResultsService:
    """Service for managing extraction results and versions in the database"""

    #: Shown when row-level extraction data was removed by retention but the user tries to migrate.
    MIGRATION_DETAIL_EXPIRED_MESSAGE = (
        "Extracted row data is no longer available (data retention). "
        "Run extraction again, then start migration."
    )

    _tables_initialized: bool = False
    _init_lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    async def _ensure_tables_exist(cls) -> None:
        """Ensure the extraction_results table exists"""
        if cls._tables_initialized:
            return

        async with cls._init_lock:
            if cls._tables_initialized:
                return

            # Create extraction_results table
            await database.execute(
                """
                CREATE TABLE IF NOT EXISTS extraction_results (
                    id VARCHAR PRIMARY KEY,
                    job_id VARCHAR UNIQUE NOT NULL,
                    parent_job_id VARCHAR,
                    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    conversation_id UUID REFERENCES chat_conversations(id) ON DELETE SET NULL,
                    
                    -- Source information
                    source_type VARCHAR(50),
                    source_name VARCHAR(255),
                    source_identifier VARCHAR(500),
                    
                    -- Extraction data
                    extracted_data JSONB NOT NULL,
                    extraction_goals TEXT,
                    document_type VARCHAR(255),
                    document_description TEXT,
                    
                    -- Metadata
                    record_count INTEGER,
                    extraction_version INTEGER DEFAULT 1,
                    has_cleanup_rules BOOLEAN DEFAULT FALSE,
                    has_transformation_rules BOOLEAN DEFAULT FALSE,
                    has_mapping_transform BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Create indexes
            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_user_id 
                ON extraction_results(user_id)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_job_id 
                ON extraction_results(job_id)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_parent_job_id 
                ON extraction_results(parent_job_id)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_conversation_id 
                ON extraction_results(conversation_id)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_user_created 
                ON extraction_results(user_id, created_at DESC)
                """
            )

            await database.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_extraction_results_parent_chain 
                ON extraction_results(parent_job_id, extraction_version)
                """
            )

            await database.execute(
                """
                ALTER TABLE extraction_results
                ADD COLUMN IF NOT EXISTS detail_purged_at TIMESTAMPTZ
                """
            )

            await database.execute(
                """
                ALTER TABLE extraction_results
                ADD COLUMN IF NOT EXISTS extraction_metadata JSONB DEFAULT '{}'::jsonb
                """
            )

            await database.execute(
                """
                ALTER TABLE extraction_results
                ADD COLUMN IF NOT EXISTS has_mapping_transform BOOLEAN DEFAULT FALSE
                """
            )

            await database.execute(
                """
                ALTER TABLE extraction_results
                ADD COLUMN IF NOT EXISTS pipeline_stage VARCHAR(32)
                """
            )

            cls._tables_initialized = True

    @classmethod
    async def ensure_schema(cls) -> None:
        """Initialize extraction_results tables once at application startup."""
        await cls._ensure_tables_exist()

    @classmethod
    async def update_pipeline_stage(
        cls,
        job_id: str,
        user_id: str,
        stage: str,
    ) -> None:
        """Persist job pipeline stage (extracted → mapped → migrating → migrated)."""
        await cls._ensure_tables_exist()
        st = (stage or "").strip().lower()
        if not st or not (job_id or "").strip():
            return
        await database.execute(
            """
            UPDATE extraction_results
            SET pipeline_stage = :stage
            WHERE job_id = :job_id AND user_id = :user_id
            """,
            values={"job_id": job_id, "user_id": user_id, "stage": st},
        )

    @classmethod
    async def _ensure_extraction_result_records_table(cls) -> None:
        """Ensure extraction_result_records table exists for scalable record storage."""
        await cls._ensure_tables_exist()
        # Table creation - uses raw SQL; extraction_results must exist first
        await database.execute(
            """
            CREATE TABLE IF NOT EXISTS extraction_result_records (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                extraction_result_id VARCHAR NOT NULL REFERENCES extraction_results(id) ON DELETE CASCADE,
                job_id VARCHAR NOT NULL,
                ordinal INTEGER NOT NULL,
                record JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_extraction_result_ordinal UNIQUE (extraction_result_id, ordinal)
            )
            """
        )
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_err_extraction_result_id ON extraction_result_records(extraction_result_id)"
        )
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_err_job_id_ordinal ON extraction_result_records(job_id, ordinal)"
        )
        await database.execute(
            "CREATE INDEX IF NOT EXISTS idx_err_job_id ON extraction_result_records(job_id)"
        )

    @staticmethod
    async def _save_records_to_extraction_result_records(
        extraction_result_id: str, job_id: str, extracted_data: List[Dict[str, Any]]
    ) -> None:
        """
        Insert each record from extracted_data into extraction_result_records.
        Uses batched inserts (500 per batch) for large datasets.
        """
        if not extracted_data or not isinstance(extracted_data, list):
            return
        try:
            from utils.batch_config import EXTRACTION_RESULT_RECORDS_BATCH_SIZE
            batch_size = EXTRACTION_RESULT_RECORDS_BATCH_SIZE
        except ImportError:
            batch_size = 500
        for batch_start in range(0, len(extracted_data), batch_size):
            batch = extracted_data[batch_start : batch_start + batch_size]
            values_list = []
            for i, rec in enumerate(batch):
                ordinal = batch_start + i
                if not isinstance(rec, dict):
                    rec = {}
                try:
                    record_json = json.dumps(rec, default=str)
                except (TypeError, ValueError):
                    record_json = "{}"
                values_list.append({
                    "extraction_result_id": extraction_result_id,
                    "job_id": job_id,
                    "ordinal": ordinal,
                    "record_value": record_json,
                })
            if values_list:
                query = """
                INSERT INTO extraction_result_records (extraction_result_id, job_id, ordinal, record)
                VALUES (:extraction_result_id, :job_id, :ordinal, CAST(:record_value AS jsonb))
                """
                await database.execute_many(query=query, values=values_list)

    @staticmethod
    async def _calculate_extraction_version(parent_job_id: Optional[str], user_id: str) -> int:
        """
        Calculate extraction_version by counting the parent chain.
        If parent_job_id is None, this is version 1.
        Otherwise, find the parent's version and add 1.
        """
        if not parent_job_id:
            return 1

        # Get parent's extraction version
        query = """
        SELECT extraction_version 
        FROM extraction_results 
        WHERE job_id = :parent_job_id AND user_id = :user_id
        LIMIT 1
        """
        
        result = await database.fetch_one(
            query,
            values={"parent_job_id": parent_job_id, "user_id": user_id}
        )
        
        if result:
            return result["extraction_version"] + 1
        
        # If parent not found, start at version 1 (shouldn't happen, but handle gracefully)
        return 1

    @staticmethod
    async def save_extraction_result(
        job_id: str,
        user_id: str,
        extracted_data: List[Dict[str, Any]],
        source_type: str,
        extraction_goals: Optional[str] = None,
        document_type: Optional[str] = None,
        document_description: Optional[str] = None,
        source_name: Optional[str] = None,
        source_identifier: Optional[str] = None,
        parent_job_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        has_cleanup_rules: bool = False,
        has_transformation_rules: bool = False,
        has_mapping_transform: bool = False,
        status: str = "success",  # "success" or "failed"
        error_message: Optional[str] = None,
        extraction_metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Save extraction result to database.
        Returns the result ID.
        """
        await ExtractionResultsService._ensure_tables_exist()

        result_id = str(uuid.uuid4())
        record_count = len(extracted_data) if isinstance(extracted_data, list) else (1 if extracted_data else 0)

        # Calculate extraction version
        extraction_version = await ExtractionResultsService._calculate_extraction_version(
            parent_job_id, user_id
        )

        # Serialize extracted_data to JSON string for JSONB column
        try:
            extracted_data_json = await asyncio.to_thread(json.dumps, extracted_data) if extracted_data else json.dumps([])
        except Exception as e:
            print(f"⚠️ Failed to serialize extracted_data: {e}")
            extracted_data_json = json.dumps({"error": "Could not serialize extraction data"})

        meta: Dict[str, Any] = dict(extraction_metadata or {})
        if source_identifier and not meta.get("source_object_api_name"):
            if (source_type or "").lower() == "salesforce":
                meta.setdefault("source_object_api_name", source_identifier)
            elif (source_type or "").lower() == "sql":
                meta.setdefault("source_table", source_identifier)

        if isinstance(extracted_data, list):
            for rec in extracted_data:
                if isinstance(rec, dict) and rec.get("semantic_data_profile"):
                    meta.setdefault("semantic_data_profile", rec.get("semantic_data_profile"))
                    break

        try:
            extraction_metadata_json = json.dumps(meta) if meta else "{}"
        except (TypeError, ValueError):
            extraction_metadata_json = "{}"

        # Prepare query
        query = """
        INSERT INTO extraction_results (
            id, job_id, parent_job_id, user_id, conversation_id,
            source_type, source_name, source_identifier,
            extracted_data, extraction_goals, document_type, document_description,
            record_count, extraction_version,
            has_cleanup_rules, has_transformation_rules, has_mapping_transform,
            extraction_metadata, created_at
        ) VALUES (
            :result_id, :job_id, :parent_job_id, :user_id, :conversation_id,
            :source_type, :source_name, :source_identifier,
            :extracted_data, :extraction_goals, :document_type, :document_description,
            :record_count, :extraction_version,
            :has_cleanup_rules, :has_transformation_rules, :has_mapping_transform,
            CAST(:extraction_metadata AS jsonb), :created_at
        )
        """

        values = {
            "result_id": result_id,
            "job_id": job_id,
            "parent_job_id": parent_job_id,
            "user_id": user_id,
            "conversation_id": conversation_id,
            "source_type": source_type,
            "source_name": source_name,
            "source_identifier": source_identifier,
            "extracted_data": extracted_data_json,
            "extraction_goals": extraction_goals,
            "document_type": document_type,
            "document_description": document_description,
            "record_count": record_count,
            "extraction_version": extraction_version,
            "has_cleanup_rules": has_cleanup_rules,
            "has_transformation_rules": has_transformation_rules,
            "has_mapping_transform": has_mapping_transform,
            "extraction_metadata": extraction_metadata_json,
            "created_at": datetime.now(timezone.utc)
        }

        try:
            await database.execute(query, values=values)
            # Also write records to extraction_result_records for scalable pagination
            if extracted_data and isinstance(extracted_data, list) and len(extracted_data) > 0:
                try:
                    await ExtractionResultsService._ensure_extraction_result_records_table()
                    await ExtractionResultsService._save_records_to_extraction_result_records(
                        extraction_result_id=result_id, job_id=job_id, extracted_data=extracted_data
                    )
                    print(f"✅ Saved {len(extracted_data)} records to extraction_result_records for job_id: {job_id}")
                    # P2: Clear extracted_data blob - records live in extraction_result_records
                    await database.execute(
                        """
                        UPDATE extraction_results SET extracted_data = '[]'::jsonb
                        WHERE id = :result_id
                        """,
                        values={"result_id": result_id},
                    )
                except Exception as records_err:
                    print(f"⚠️ Failed to save to extraction_result_records (extraction_results saved): {records_err}")
            print(f"✅ Saved extraction result for job_id: {job_id}, version: {extraction_version}")
            if status == "success":
                try:
                    await ExtractionResultsService.update_pipeline_stage(
                        job_id, str(user_id), "extracted"
                    )
                except Exception as stage_err:
                    print(f"⚠️ Failed to set pipeline_stage=extracted for {job_id}: {stage_err}")
            try:
                from services.flow_executions_service import FlowExecutionsService

                await FlowExecutionsService.link_extraction_job_after_save(job_id)
            except Exception as link_err:
                print(f"⚠️ flow_executions link after extraction save: {link_err}")
            return result_id
        except Exception as e:
            print(f"❌ Failed to save extraction result: {e}")
            # Don't raise - extraction should continue even if saving fails
            return result_id

    @staticmethod
    async def resolve_job_id(identifier: str, user_id: str) -> Optional[str]:
        """
        Resolve extraction_results.job_id from either the async job_id or the
        extraction_results.id (primary key). Used for stable deep links and APIs.
        """
        if not identifier or not user_id:
            return None
        await ExtractionResultsService._ensure_tables_exist()
        row = await database.fetch_one(
            """
            SELECT job_id FROM extraction_results
            WHERE job_id = :id AND user_id = :uid
            LIMIT 1
            """,
            {"id": identifier, "uid": user_id},
        )
        if row:
            return row["job_id"]
        row = await database.fetch_one(
            """
            SELECT job_id FROM extraction_results
            WHERE id = :id AND user_id = :uid
            LIMIT 1
            """,
            {"id": identifier, "uid": user_id},
        )
        if row:
            return row["job_id"]
        return None

    @staticmethod
    async def is_extraction_detail_expired(job_id: str, user_id: str) -> bool:
        """True when retention has cleared row-level extraction detail for this job."""
        resolved = await ExtractionResultsService.resolve_job_id(job_id, user_id)
        if not resolved:
            return False
        await ExtractionResultsService._ensure_tables_exist()
        row = await database.fetch_one(
            """
            SELECT detail_purged_at FROM extraction_results
            WHERE job_id = :job_id AND user_id = :user_id
            LIMIT 1
            """,
            values={"job_id": resolved, "user_id": user_id},
        )
        if not row:
            return False
        return dict(row).get("detail_purged_at") is not None

    @staticmethod
    async def get_extraction_record_count(job_id: str, user_id: str) -> Optional[int]:
        """
        Get record count for an extraction by job_id.
        Lightweight - does not load extracted_data. Returns None if job not found.
        """
        await ExtractionResultsService._ensure_tables_exist()
        result = await database.fetch_one(
            """
            SELECT record_count FROM extraction_results
            WHERE job_id = :job_id AND user_id = :user_id LIMIT 1
            """,
            values={"job_id": job_id, "user_id": user_id},
        )
        if not result or result["record_count"] is None:
            return None
        return int(result["record_count"])

    @staticmethod
    async def wait_for_extraction_records_persisted(
        job_id: str,
        user_id: str,
        *,
        timeout_seconds: float = 600.0,
        poll_interval: float = 0.25,
    ) -> bool:
        """
        Block until extraction rows are ready for migration, or timeout.

        Returns False immediately if row-level extraction detail was purged (retention).

        Normal path: extraction_result_rows COUNT >= extraction_results.record_count.
        Legacy: full list still in extracted_data JSON before/without row split.

        Used so migration does not start while async saves are still inserting rows.
        """
        if await ExtractionResultsService.is_extraction_detail_expired(job_id, user_id):
            print(
                f"⚠️ wait_for_extraction_records_persisted: detail purged, not waiting "
                f"(job_id={job_id})"
            )
            return False

        expected = await ExtractionResultsService.get_extraction_record_count(job_id, user_id)
        if expected is None or expected <= 0:
            return True

        await ExtractionResultsService._ensure_extraction_result_records_table()
        deadline = time.monotonic() + timeout_seconds

        def _count_value(row) -> int:
            # databases.Record has __getitem__ but not .get — avoid row.get(...)
            if row is None:
                return 0
            try:
                v = row["c"]
                return int(v) if v is not None else 0
            except (KeyError, TypeError, ValueError):
                return 0

        while time.monotonic() < deadline:
            row = await database.fetch_one(
                """
                SELECT COUNT(*) AS c
                FROM extraction_result_records
                WHERE job_id = :job_id
                """,
                values={"job_id": job_id},
            )
            n = _count_value(row)
            if n >= expected:
                return True

            meta = await database.fetch_one(
                """
                SELECT extracted_data, record_count
                FROM extraction_results
                WHERE job_id = :job_id AND user_id = :user_id
                LIMIT 1
                """,
                values={"job_id": job_id, "user_id": user_id},
            )
            if meta:
                ed = meta["extracted_data"]
                if isinstance(ed, str):
                    try:
                        ed = json.loads(ed)
                    except (json.JSONDecodeError, TypeError):
                        ed = None
                if isinstance(ed, list) and len(ed) >= expected:
                    return True

            await asyncio.sleep(poll_interval)

        row = await database.fetch_one(
            "SELECT COUNT(*) AS c FROM extraction_result_records WHERE job_id = :job_id",
            values={"job_id": job_id},
        )
        n = _count_value(row)
        print(
            f"⚠️ wait_for_extraction_records_persisted timeout: job_id={job_id} "
            f"have {n} rows, expected {expected}"
        )
        return False

    @staticmethod
    async def _resolve_lineage_root_job_id(job_id: str, user_id: str) -> Optional[str]:
        """Walk parent_job_id chain to the root extraction job_id."""
        await ExtractionResultsService._ensure_tables_exist()
        current = job_id
        seen = set()
        for _ in range(64):
            if current in seen:
                break
            seen.add(current)
            row = await database.fetch_one(
                """
                SELECT job_id, parent_job_id
                FROM extraction_results
                WHERE job_id = :job_id AND user_id = :user_id
                LIMIT 1
                """,
                values={"job_id": current, "user_id": user_id},
            )
            if not row:
                return current if current == job_id else None
            parent = row["parent_job_id"]
            if not parent:
                return row["job_id"]
            current = parent
        return current

    @staticmethod
    async def get_lineage_job_ids(job_id: str, user_id: str) -> List[str]:
        """All job_ids in a version lineage (root → leaves), ordered by extraction_version."""
        root = await ExtractionResultsService._resolve_lineage_root_job_id(job_id, user_id)
        if not root:
            return []
        await ExtractionResultsService._ensure_tables_exist()
        rows = await database.fetch_all(
            """
            WITH RECURSIVE lineage AS (
                SELECT job_id, parent_job_id, extraction_version, created_at
                FROM extraction_results
                WHERE job_id = :root_id AND user_id = :user_id
                UNION ALL
                SELECT er.job_id, er.parent_job_id, er.extraction_version, er.created_at
                FROM extraction_results er
                INNER JOIN lineage l ON er.parent_job_id = l.job_id
                WHERE er.user_id = :user_id
            )
            SELECT job_id, extraction_version, created_at
            FROM lineage
            ORDER BY extraction_version ASC, created_at ASC
            """,
            values={"root_id": root, "user_id": user_id},
        )
        return [r["job_id"] for r in rows] if rows else [root]

    @staticmethod
    async def get_latest_lineage_job_id(job_id: str, user_id: str) -> Optional[str]:
        lineage = await ExtractionResultsService.get_lineage_job_ids(job_id, user_id)
        return lineage[-1] if lineage else None

    @staticmethod
    async def get_extraction_result(
        job_id: str,
        user_id: str,
        version: Optional[int] = None,
        *,
        prefer_lineage_latest: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve stored extraction result by job_id or extraction_results.id.
        Verifies user ownership.

        When version is set, resolves the job in the same lineage as job_id with that
        extraction_version. When version is None and prefer_lineage_latest is True,
        returns the latest version in that lineage. Set prefer_lineage_latest=False
        to load the exact job row (used by migration row hints).
        """
        resolved = await ExtractionResultsService.resolve_job_id(job_id, user_id)
        if not resolved:
            return None
        job_id = resolved

        if version is not None:
            lineage_ids = await ExtractionResultsService.get_lineage_job_ids(job_id, user_id)
            for lid in lineage_ids:
                row = await database.fetch_one(
                    """
                    SELECT extraction_version FROM extraction_results
                    WHERE job_id = :job_id AND user_id = :user_id
                    LIMIT 1
                    """,
                    values={"job_id": lid, "user_id": user_id},
                )
                if row and int(row["extraction_version"]) == int(version):
                    job_id = lid
                    break
        elif prefer_lineage_latest:
            latest = await ExtractionResultsService.get_latest_lineage_job_id(job_id, user_id)
            if latest:
                job_id = latest

        await ExtractionResultsService._ensure_tables_exist()

        query = """
        SELECT 
            id, job_id, parent_job_id, user_id, conversation_id,
            source_type, source_name, source_identifier,
            extracted_data, extraction_goals, document_type, document_description,
            record_count, extraction_version,
            has_cleanup_rules, has_transformation_rules, has_mapping_transform,
            extraction_metadata, created_at, detail_purged_at, pipeline_stage
        FROM extraction_results
        WHERE job_id = :job_id AND user_id = :user_id
        LIMIT 1
        """

        result = await database.fetch_one(
            query,
            values={"job_id": job_id, "user_id": user_id}
        )

        if not result:
            return None

        detail_purged_at = dict(result).get("detail_purged_at")
        def _parse_extraction_metadata(row) -> Dict[str, Any]:
            raw = dict(row).get("extraction_metadata")
            if isinstance(raw, dict):
                return raw
            if isinstance(raw, str) and raw.strip():
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    return {}
            return {}

        if detail_purged_at is not None:
            return {
                "id": result["id"],
                "job_id": result["job_id"],
                "parent_job_id": result["parent_job_id"],
                "user_id": str(result["user_id"]),
                "conversation_id": str(result["conversation_id"]) if result["conversation_id"] else None,
                "source_type": result["source_type"],
                "source_name": result["source_name"],
                "source_identifier": result["source_identifier"],
                "extracted_data": [],
                "extraction_goals": result["extraction_goals"],
                "document_type": result["document_type"],
                "document_description": result["document_description"],
                "extraction_metadata": _parse_extraction_metadata(result),
                "record_count": result["record_count"],
                "extraction_version": result["extraction_version"],
                "has_cleanup_rules": result["has_cleanup_rules"],
                "has_transformation_rules": result["has_transformation_rules"],
                "has_mapping_transform": dict(result).get("has_mapping_transform", False),
                "pipeline_stage": dict(result).get("pipeline_stage"),
                "created_at": result["created_at"].isoformat() if result["created_at"] else None,
                "detail_available": False,
                "detail_expired": True,
            }

        # Parse JSONB data
        extracted_data = result["extracted_data"]
        if isinstance(extracted_data, str):
            try:
                extracted_data = json.loads(extracted_data)
            except Exception:
                extracted_data = []

        # P2: When blob is empty but records exist in extraction_result_records, load from there
        record_count = result["record_count"] or 0
        if (not extracted_data or (isinstance(extracted_data, list) and len(extracted_data) == 0)) and record_count > 0:
            await ExtractionResultsService._ensure_extraction_result_records_table()
            extracted_data = []
            last_ordinal = -1
            chunk_size = 5000
            while True:
                rows = await database.fetch_all(
                    """
                    SELECT ordinal, record FROM extraction_result_records
                    WHERE job_id = :job_id AND ordinal > :last_ordinal
                    ORDER BY ordinal
                    LIMIT :limit
                    """,
                    values={"job_id": job_id, "last_ordinal": last_ordinal, "limit": chunk_size},
                )
                if not rows:
                    break
                for row in rows:
                    rec = row["record"]
                    if isinstance(rec, str):
                        try:
                            rec = json.loads(rec)
                        except (json.JSONDecodeError, TypeError):
                            rec = {}
                    extracted_data.append(rec)
                    last_ordinal = row["ordinal"]
                if len(rows) < chunk_size:
                    break

        ext_meta = _parse_extraction_metadata(result)
        return {
            "id": result["id"],
            "job_id": result["job_id"],
            "parent_job_id": result["parent_job_id"],
            "user_id": str(result["user_id"]),
            "conversation_id": str(result["conversation_id"]) if result["conversation_id"] else None,
            "source_type": result["source_type"],
            "source_name": result["source_name"],
            "source_identifier": result["source_identifier"],
            "extracted_data": extracted_data,
            "extraction_goals": result["extraction_goals"],
            "document_type": result["document_type"],
            "document_description": result["document_description"],
            "extraction_metadata": ext_meta,
            "source_document_type": result["document_type"],
            "source_document_description": result["document_description"],
            "record_count": result["record_count"],
            "extraction_version": result["extraction_version"],
            "has_cleanup_rules": result["has_cleanup_rules"],
            "has_transformation_rules": result["has_transformation_rules"],
            "has_mapping_transform": dict(result).get("has_mapping_transform", False),
            "pipeline_stage": dict(result).get("pipeline_stage"),
            "created_at": result["created_at"].isoformat() if result["created_at"] else None,
            "detail_available": True,
            "detail_expired": False,
        }

    @staticmethod
    async def get_extraction_results_by_conversation(
        conversation_id: str,
        user_id: str,
        limit: int = 50,
        metadata_only: bool = True,
        *,
        conversation_verified: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Get extraction results for a conversation.
        When metadata_only=True (default), does NOT select extracted_data to reduce payload and DB load.
        Full data is available via get_extraction_result(job_id) or get_extraction_records_paginated.
        """
        await ExtractionResultsService._ensure_tables_exist()
        if not conversation_verified:
            from services.chat_service import ChatService

            conversation = await ChatService.get_conversation(conversation_id, user_id)
            if not conversation:
                return []

        if metadata_only:
            query = """
            SELECT 
                id, job_id, parent_job_id, user_id, conversation_id,
                source_type, source_name, source_identifier,
                extraction_goals, document_type, document_description,
                record_count, extraction_version,
                has_cleanup_rules, has_transformation_rules, has_mapping_transform,
                created_at, detail_purged_at
            FROM extraction_results
            WHERE conversation_id = :conversation_id AND user_id = :user_id
            ORDER BY created_at DESC
            LIMIT :limit
            """
        else:
            query = """
            SELECT 
                id, job_id, parent_job_id, user_id, conversation_id,
                source_type, source_name, source_identifier,
                extracted_data, extraction_goals, document_type, document_description,
                record_count, extraction_version,
                has_cleanup_rules, has_transformation_rules, has_mapping_transform,
                created_at, detail_purged_at
            FROM extraction_results
            WHERE conversation_id = :conversation_id AND user_id = :user_id
            ORDER BY created_at DESC
            LIMIT :limit
            """

        async def _fetch():
            return await database.fetch_all(
                query,
                values={"conversation_id": conversation_id, "user_id": user_id, "limit": limit},
            )

        from db.retry import with_db_retry

        results = await with_db_retry(_fetch)

        formatted_results = []
        for result in results:
            dp = dict(result).get("detail_purged_at")
            row = {
                "id": result["id"],
                "job_id": result["job_id"],
                "parent_job_id": result["parent_job_id"],
                "user_id": str(result["user_id"]),
                "conversation_id": str(result["conversation_id"]) if result["conversation_id"] else None,
                "source_type": result["source_type"],
                "source_name": result["source_name"],
                "source_identifier": result["source_identifier"],
                "extraction_goals": result["extraction_goals"],
                "document_type": result["document_type"],
                "document_description": result["document_description"],
                "record_count": result["record_count"],
                "extraction_version": result["extraction_version"],
                "has_cleanup_rules": result["has_cleanup_rules"],
                "has_transformation_rules": result["has_transformation_rules"],
                "has_mapping_transform": dict(result).get("has_mapping_transform", False),
                "created_at": result["created_at"].isoformat() if result["created_at"] else None,
                "detail_available": dp is None,
                "detail_expired": dp is not None,
            }
            if not metadata_only and "extracted_data" in result:
                extracted_data = result["extracted_data"]
                if isinstance(extracted_data, str):
                    try:
                        extracted_data = json.loads(extracted_data)
                    except Exception:
                        extracted_data = []
                row["extracted_data"] = extracted_data
            formatted_results.append(row)

        return formatted_results

    @staticmethod
    async def get_extraction_records_paginated(
        job_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 100,
        use_extraction_result_records: bool = True,
        search: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve extraction records with pagination.
        Prefers extraction_result_records when available; falls back to extracted_data.
        job_id may be the async job id or extraction_results.id.
        Returns metadata + paginated records, or None if job not found.
        """
        resolved = await ExtractionResultsService.resolve_job_id(job_id, user_id)
        if not resolved:
            return None
        job_id = resolved

        await ExtractionResultsService._ensure_tables_exist()

        # First get metadata (and verify user ownership) from extraction_results
        meta_query = """
        SELECT id, job_id, parent_job_id, user_id, conversation_id,
               source_type, source_name, source_identifier,
               extraction_goals, document_type, document_description,
               record_count, extraction_version,
               has_cleanup_rules, has_transformation_rules, has_mapping_transform,
               created_at, detail_purged_at
        FROM extraction_results
        WHERE job_id = :job_id AND user_id = :user_id
        LIMIT 1
        """
        result = await database.fetch_one(
            meta_query,
            values={"job_id": job_id, "user_id": user_id}
        )
        if not result:
            return None

        if dict(result).get("detail_purged_at") is not None:
            meta = {
                "id": result["id"],
                "job_id": result["job_id"],
                "parent_job_id": result["parent_job_id"],
                "user_id": str(result["user_id"]),
                "conversation_id": str(result["conversation_id"]) if result["conversation_id"] else None,
                "source_type": result["source_type"],
                "source_name": result["source_name"],
                "source_identifier": result["source_identifier"],
                "extraction_goals": result["extraction_goals"],
                "document_type": result["document_type"],
                "document_description": result["document_description"],
                "record_count": result["record_count"] or 0,
                "extraction_version": result["extraction_version"],
                "has_cleanup_rules": result["has_cleanup_rules"],
                "has_transformation_rules": result["has_transformation_rules"],
                "has_mapping_transform": dict(result).get("has_mapping_transform", False),
                "created_at": result["created_at"].isoformat() if result["created_at"] else None,
                "detail_available": False,
                "detail_expired": True,
            }
            return {
                "detail_expired": True,
                "metadata": meta,
                "records": [],
                "total_count": result["record_count"] or 0,
                "page": page,
                "page_size": page_size,
            }

        total_count = result["record_count"] or 0
        page = max(1, page)
        page_size = min(max(1, page_size), 1000)
        offset = (page - 1) * page_size
        search_pattern = f"%{search.strip()}%" if search and str(search).strip() else None

        metadata = {
            "id": result["id"],
            "job_id": result["job_id"],
            "parent_job_id": result["parent_job_id"],
            "user_id": str(result["user_id"]),
            "conversation_id": str(result["conversation_id"]) if result["conversation_id"] else None,
            "source_type": result["source_type"],
            "source_name": result["source_name"],
            "source_identifier": result["source_identifier"],
            "extraction_goals": result["extraction_goals"],
            "document_type": result["document_type"],
            "document_description": result["document_description"],
            "record_count": total_count,
            "extraction_version": result["extraction_version"],
            "has_cleanup_rules": result["has_cleanup_rules"],
            "has_transformation_rules": result["has_transformation_rules"],
            "has_mapping_transform": dict(result).get("has_mapping_transform", False),
            "created_at": result["created_at"].isoformat() if result["created_at"] else None,
            "detail_available": True,
            "detail_expired": False,
        }

        records = []

        if use_extraction_result_records:
            await ExtractionResultsService._ensure_extraction_result_records_table()
            if search_pattern:
                count_row = await database.fetch_one(
                    """
                    SELECT COUNT(*) AS c
                    FROM extraction_result_records
                    WHERE job_id = :job_id
                      AND record::text ILIKE :search_pattern
                    """,
                    values={"job_id": job_id, "search_pattern": search_pattern},
                )
                total_count = int(count_row["c"]) if count_row and count_row["c"] is not None else 0
            recs_query = """
            SELECT ordinal, record
            FROM extraction_result_records
            WHERE job_id = :job_id
            """
            if search_pattern:
                recs_query += "\n              AND record::text ILIKE :search_pattern"
            recs_query += """
            ORDER BY ordinal
            LIMIT :limit OFFSET :offset
            """
            query_values = {"job_id": job_id, "limit": page_size, "offset": offset}
            if search_pattern:
                query_values["search_pattern"] = search_pattern
            rows = await database.fetch_all(recs_query, values=query_values)
            for row in rows:
                rec = row["record"]
                if isinstance(rec, str):
                    try:
                        rec = json.loads(rec)
                    except (json.JSONDecodeError, TypeError):
                        rec = {}
                records.append(strip_row_display_metadata(rec))

        if not records and total_count > 0:
            # Fallback: read from extracted_data (legacy or records table empty)
            full_result = await ExtractionResultsService.get_extraction_result(job_id, user_id)
            if full_result and full_result.get("extracted_data"):
                all_data = full_result["extracted_data"]
                raw_slice = all_data[offset : offset + page_size]
                records = [
                    strip_row_display_metadata(r) if isinstance(r, dict) else r for r in raw_slice
                ]

        return {
            "metadata": metadata,
            "records": records,
            "total_count": total_count,
            "page": page,
            "page_size": page_size,
            "detail_expired": False,
            "search": search.strip() if search and str(search).strip() else None,
        }

    @staticmethod
    async def iterate_extraction_records(
        job_id: str,
        user_id: str,
        batch_size: Optional[int] = None,
        destination: Optional[str] = None,
        record_ordinals: Optional[List[int]] = None,
    ) -> AsyncIterator[List[Dict[str, Any]]]:
        """
        Yield batches of records from extraction_result_records for scalable migration.
        Prefers extraction_result_records; falls back to extraction_results.extracted_data in chunks.
        When record_ordinals is set, only those ordinals are yielded (recovery retry scope).
        Verifies user ownership via extraction_results.
        When batch_size is None, uses compute_migration_batch_size(total_count, destination).
        """
        await ExtractionResultsService._ensure_tables_exist()

        resolved = await ExtractionResultsService.resolve_job_id(job_id, user_id)
        if not resolved:
            return
        job_id = resolved

        meta_query = """
        SELECT id, record_count, detail_purged_at
        FROM extraction_results
        WHERE job_id = :job_id AND user_id = :user_id
        LIMIT 1
        """
        result = await database.fetch_one(
            meta_query,
            values={"job_id": job_id, "user_id": user_id}
        )
        if not result:
            return

        if dict(result).get("detail_purged_at") is not None:
            return

        if record_ordinals:
            ordinals = sorted({int(o) for o in record_ordinals})
            if not ordinals:
                return
            if batch_size is None:
                try:
                    from utils.batch_config import compute_migration_batch_size
                    batch_size = compute_migration_batch_size(
                        len(ordinals), (destination or "salesforce")
                    )
                except ImportError:
                    batch_size = 500
            await ExtractionResultsService._ensure_extraction_result_records_table()
            for start in range(0, len(ordinals), batch_size):
                chunk = ordinals[start : start + batch_size]
                placeholders = ", ".join(f":o{i}" for i in range(len(chunk)))
                values = {"job_id": job_id}
                values.update({f"o{i}": chunk[i] for i in range(len(chunk))})
                recs_query = f"""
                SELECT ordinal, record
                FROM extraction_result_records
                WHERE job_id = :job_id AND ordinal IN ({placeholders})
                ORDER BY ordinal
                """
                rows = await database.fetch_all(recs_query, values=values)
                batch = []
                for row in rows or []:
                    rec = row["record"]
                    if isinstance(rec, str):
                        try:
                            rec = json.loads(rec)
                        except (json.JSONDecodeError, TypeError):
                            rec = {}
                    batch.append(strip_row_display_metadata(rec))
                if batch:
                    yield batch
            return

        total_count = result["record_count"] or 0
        if total_count == 0:
            return

        if batch_size is None:
            try:
                from utils.batch_config import compute_migration_batch_size
                batch_size = compute_migration_batch_size(
                    total_count, (destination or "salesforce")
                )
            except ImportError:
                batch_size = 500

        await ExtractionResultsService._ensure_extraction_result_records_table()

        # Keyset pagination: WHERE ordinal > last_ordinal - avoids OFFSET degradation for large datasets
        last_ordinal = -1
        yielded_any = False
        while True:
            recs_query = """
            SELECT ordinal, record
            FROM extraction_result_records
            WHERE job_id = :job_id AND ordinal > :last_ordinal
            ORDER BY ordinal
            LIMIT :limit
            """
            rows = await database.fetch_all(
                recs_query,
                values={"job_id": job_id, "last_ordinal": last_ordinal, "limit": batch_size}
            )
            if not rows:
                break

            batch = []
            for row in rows:
                rec = row["record"]
                if isinstance(rec, str):
                    try:
                        rec = json.loads(rec)
                    except (json.JSONDecodeError, TypeError):
                        rec = {}
                batch.append(strip_row_display_metadata(rec))
                last_ordinal = row["ordinal"]

            if batch:
                yield batch
                yielded_any = True
            # Do not stop when len(rows) < batch_size: short pages happen while rows are still
            # being inserted, or on the last page; only an empty fetch ends the stream.

        # Fallback: no rows in extraction_result_records (legacy), read from extracted_data in chunks
        if not yielded_any and total_count > 0:
            full_result = await ExtractionResultsService.get_extraction_result(job_id, user_id)
            if full_result and full_result.get("extracted_data"):
                all_data = full_result["extracted_data"]
                for chunk_start in range(0, len(all_data), batch_size):
                    yield all_data[chunk_start : chunk_start + batch_size]

    @staticmethod
    async def load_extraction_data_for_migration(
        job_id: str,
        user_id: str,
        batch_size: Optional[int] = None,
        destination: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Load all extraction records for migration using iterate_extraction_records.
        Prefers extraction_result_records (streams in batches); falls back to extracted_data.
        Use this instead of get_extraction_result when only extracted_data is needed for migration.
        When batch_size is None, uses dynamic batch sizing based on total count and destination.
        """
        records = []
        async for batch in ExtractionResultsService.iterate_extraction_records(
            job_id=job_id, user_id=user_id, batch_size=batch_size, destination=destination
        ):
            records.extend(batch)
        return records

    @staticmethod
    def _field_key_to_label(key: str) -> str:
        """Match frontend InlineDataPreview camelCaseToLabel behavior closely."""
        if not key:
            return ""
        s = key.replace("_", " ")
        s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
        return s[:1].upper() + s[1:] if s else s

    @staticmethod
    async def collect_extraction_record_column_keys(job_id: str, user_id: str) -> List[str]:
        """Stable key order: first-seen across all records; skips keys starting with '_'."""
        ordered: List[str] = []
        seen = set()
        async for batch in ExtractionResultsService.iterate_extraction_records(
            job_id=job_id, user_id=user_id, batch_size=500
        ):
            for rec in batch:
                if not rec or not isinstance(rec, dict):
                    continue
                for k in rec.keys():
                    if k.startswith("_") or k in DISPLAY_METADATA_KEYS:
                        continue
                    if k not in seen:
                        seen.add(k)
                        ordered.append(k)
        return ordered

    @staticmethod
    async def iter_extraction_csv_bytes(job_id: str, user_id: str) -> AsyncIterator[bytes]:
        """
        Stream UTF-8 CSV chunks (BOM + header on first chunk(s), then rows).
        Uses two passes over iterate_extraction_records: keys, then rows.
        job_id may be the async job id or extraction_results.id.
        """
        resolved = await ExtractionResultsService.resolve_job_id(job_id, user_id)
        if not resolved:
            return
        job_id = resolved

        keys = await ExtractionResultsService.collect_extraction_record_column_keys(job_id, user_id)

        def cell_val(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, (dict, list)):
                try:
                    return json.dumps(v, ensure_ascii=False)
                except (TypeError, ValueError):
                    return str(v)
            return str(v)

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        header_labels = [ExtractionResultsService._field_key_to_label(k) for k in keys]
        writer.writerow(header_labels)
        yield ("\ufeff" + buffer.getvalue()).encode("utf-8")
        buffer.seek(0)
        buffer.truncate(0)

        async for batch in ExtractionResultsService.iterate_extraction_records(
            job_id=job_id, user_id=user_id, batch_size=500
        ):
            for rec in batch:
                if not rec or not isinstance(rec, dict):
                    continue
                writer.writerow([cell_val(rec.get(k)) for k in keys])
                yield buffer.getvalue().encode("utf-8")
                buffer.seek(0)
                buffer.truncate(0)

