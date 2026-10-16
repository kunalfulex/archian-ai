"""
Persist per-run history for saved flows (manual vs scheduled) in flow_executions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence
from uuid import UUID

from db.connection import database


class FlowExecutionsService:
    @staticmethod
    def _parse_flow_uuid(flow_id: str) -> UUID:
        return UUID(str(flow_id).strip())

    @staticmethod
    async def create_execution(
        user_id: str,
        flow_id: str,
        trigger_type: str = "manual",
    ) -> Optional[str]:
        """
        Insert a flow_executions row (status=running). Returns UUID string or None on failure.
        """
        try:
            fid = FlowExecutionsService._parse_flow_uuid(flow_id)
            uid = UUID(str(user_id).strip()) if not isinstance(user_id, UUID) else user_id
        except (ValueError, TypeError) as e:
            print(f"⚠️ flow_executions: invalid flow_id or user_id: {e}")
            return None

        row = await database.fetch_one(
            """
            INSERT INTO flow_executions (user_id, flow_id, trigger_type, status, started_at)
            VALUES (:user_id, :flow_id, :trigger_type, 'running', NOW())
            RETURNING id::text AS id
            """,
            {
                "user_id": uid,
                "flow_id": fid,
                "trigger_type": (trigger_type or "manual")[:32],
            },
        )
        if not row:
            return None
        return row["id"]

    @staticmethod
    async def link_extraction_job_after_save(job_id: str) -> None:
        """
        After extraction_results row exists for job_id, set flow_executions.extraction_job_id
        if flow_execution_id was stored on extraction_jobs or sql_tasks.
        """
        if not job_id:
            return
        feid: Optional[str] = None
        try:
            from utils.job_stores import extraction_jobs

            j = await extraction_jobs.get(job_id, {}) or {}
            feid = j.get("flow_execution_id")
        except Exception:
            pass
        if not feid:
            try:
                from services.sql_background_processing_service import sql_tasks

                t = await sql_tasks.get(job_id, {}) or {}
                feid = t.get("flow_execution_id")
            except Exception:
                pass
        if not feid:
            return
        try:
            await database.execute(
                """
                UPDATE flow_executions
                SET extraction_job_id = :job_id
                WHERE id = CAST(:eid AS uuid)
                  AND (extraction_job_id IS NULL OR extraction_job_id = :job_id)
                """,
                {"job_id": job_id, "eid": feid},
            )
        except Exception as e:
            print(f"⚠️ flow_executions link extraction_job_id: {e}")

    @staticmethod
    async def link_migration_batch(execution_id: str, batch_id: str) -> None:
        if not execution_id or not batch_id:
            return
        try:
            await database.execute(
                """
                UPDATE flow_executions
                SET migration_batch_id = :batch_id
                WHERE id = CAST(:eid AS uuid)
                """,
                {"batch_id": batch_id, "eid": execution_id},
            )
        except Exception as e:
            print(f"⚠️ flow_executions link migration_batch_id: {e}")

    @staticmethod
    async def complete_execution(
        execution_id: str,
        status: str,
        error_summary: Optional[str] = None,
        sync_outcome: Optional[str] = None,
    ) -> None:
        if not execution_id:
            return
        try:
            await database.execute(
                """
                UPDATE flow_executions
                SET status = :status,
                    completed_at = NOW(),
                    error_summary = :err,
                    sync_outcome = :sync_outcome
                WHERE id = CAST(:eid AS uuid)
                """,
                {
                    "status": (status or "succeeded")[:32],
                    "err": error_summary,
                    "sync_outcome": sync_outcome,
                    "eid": execution_id,
                },
            )
        except Exception as e:
            print(f"⚠️ flow_executions complete: {e}")

    @staticmethod
    async def list_for_flow(
        flow_id: str,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return execution rows for a flow (newest first)."""
        try:
            fid = FlowExecutionsService._parse_flow_uuid(flow_id)
            uid = UUID(str(user_id).strip()) if not isinstance(user_id, UUID) else user_id
        except (ValueError, TypeError):
            return []

        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        rows = await database.fetch_all(
            """
            SELECT
                fe.id::text AS id,
                fe.flow_id::text AS flow_id,
                fe.trigger_type,
                fe.status,
                fe.extraction_job_id,
                er.id::text AS extraction_result_id,
                er.record_count AS extraction_record_count,
                fe.migration_batch_id,
                fe.sync_outcome,
                fe.started_at,
                fe.completed_at,
                fe.error_summary,
                fe.created_at
            FROM flow_executions fe
            LEFT JOIN extraction_results er
                ON er.job_id = fe.extraction_job_id AND er.user_id = fe.user_id
            WHERE fe.flow_id = :flow_id AND fe.user_id = :user_id
            ORDER BY fe.started_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """,
            {"flow_id": fid, "user_id": uid, "limit": limit, "offset": offset},
        )
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            d = dict(r)
            for k in ("started_at", "completed_at", "created_at"):
                v = d.get(k)
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            out.append(d)
        return out

    @staticmethod
    async def list_for_flow_ids(
        flow_ids: Sequence[str],
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Return execution rows for one or more flows (same user), newest first."""
        ids_u: List[UUID] = []
        for raw in flow_ids or []:
            if not raw:
                continue
            try:
                ids_u.append(UUID(str(raw).strip()))
            except (ValueError, TypeError):
                continue
        if not ids_u:
            return []

        uid = UUID(str(user_id).strip()) if not isinstance(user_id, UUID) else user_id
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        rows = await database.fetch_all(
            """
            SELECT
                fe.id::text AS id,
                fe.flow_id::text AS flow_id,
                fe.trigger_type,
                fe.status,
                fe.extraction_job_id,
                er.id::text AS extraction_result_id,
                er.record_count AS extraction_record_count,
                fe.migration_batch_id,
                fe.sync_outcome,
                fe.started_at,
                fe.completed_at,
                fe.error_summary,
                fe.created_at
            FROM flow_executions fe
            LEFT JOIN extraction_results er
                ON er.job_id = fe.extraction_job_id AND er.user_id = fe.user_id
            WHERE fe.user_id = :user_id AND fe.flow_id = ANY(:flow_ids)
            ORDER BY fe.started_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """,
            {"user_id": uid, "flow_ids": ids_u, "limit": limit, "offset": offset},
        )
        out: List[Dict[str, Any]] = []
        for r in rows or []:
            d = dict(r)
            for k in ("started_at", "completed_at", "created_at"):
                v = d.get(k)
                if hasattr(v, "isoformat"):
                    d[k] = v.isoformat()
            out.append(d)
        return out
