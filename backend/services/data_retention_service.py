"""
Purge extraction row-level data, migration row-level data, and file uploads past per-user retention.
Caps: standard plans max 30 days; non-standard (enterprise/custom) plan_limits up to 365 days.
"""

import asyncio
from typing import Any, Dict, Optional, Tuple

from db.connection import database
from services.file_store import delete_key

STANDARD_MAX_RETENTION_DAYS = 30
ENTERPRISE_MAX_RETENTION_DAYS = 365


class DataRetentionService:
    @staticmethod
    async def get_max_retention_days_for_user(user_id: str) -> int:
        """
        Enterprise/custom plans (plan_limits.is_standard_plan = false) may retain up to 365 days.
        All standard plans are capped at 30 days for this setting.
        """
        row = await database.fetch_one(
            """
            SELECT pl.is_standard_plan
            FROM user_plans up
            JOIN plan_limits pl ON up.plan_type = pl.plan_type
            WHERE up.user_id = CAST(:user_id AS uuid) AND up.status = 'active'
            LIMIT 1
            """,
            values={"user_id": user_id},
        )
        if not row:
            return STANDARD_MAX_RETENTION_DAYS
        is_standard = row["is_standard_plan"]
        if is_standard is False:
            return ENTERPRISE_MAX_RETENTION_DAYS
        # NULL or True: treat as standard catalog plan
        return STANDARD_MAX_RETENTION_DAYS

    @staticmethod
    async def get_effective_retention_days(user_id: str) -> int:
        """Stored preference clamped to the user's plan max."""
        max_days = await DataRetentionService.get_max_retention_days_for_user(user_id)
        row = await database.fetch_one(
            """
            SELECT COALESCE(data_retention_days, :default_days) AS d
            FROM users WHERE id = CAST(:user_id AS uuid)
            """,
            values={"user_id": user_id, "default_days": STANDARD_MAX_RETENTION_DAYS},
        )
        if not row:
            return min(STANDARD_MAX_RETENTION_DAYS, max_days)
        stored = int(row["d"] or STANDARD_MAX_RETENTION_DAYS)
        return max(1, min(stored, max_days))

    @staticmethod
    async def get_settings(user_id: str) -> Dict[str, Any]:
        max_days = await DataRetentionService.get_max_retention_days_for_user(user_id)
        effective = await DataRetentionService.get_effective_retention_days(user_id)
        row = await database.fetch_one(
            """
            SELECT data_retention_days FROM users WHERE id = CAST(:user_id AS uuid)
            """,
            values={"user_id": user_id},
        )
        stored = int(row["data_retention_days"]) if row and row["data_retention_days"] is not None else STANDARD_MAX_RETENTION_DAYS
        return {
            "data_retention_days": stored,
            "effective_data_retention_days": effective,
            "max_allowed_days": max_days,
        }

    @staticmethod
    async def validate_and_update_retention_days(user_id: str, days: int) -> Tuple[bool, str]:
        if days < 1:
            return False, "data_retention_days must be at least 1"
        max_days = await DataRetentionService.get_max_retention_days_for_user(user_id)
        if days > max_days:
            return False, f"data_retention_days cannot exceed {max_days} for your plan"
        await database.execute(
            """
            UPDATE users SET data_retention_days = :days, updated_at = NOW()
            WHERE id = CAST(:user_id AS uuid)
            """,
            values={"user_id": user_id, "days": days},
        )
        return True, ""

    @staticmethod
    async def _delete_s3_key_if_file_source(job_id: str, source_type: Optional[str], source_identifier: Optional[str]) -> None:
        if (source_type or "").lower() != "file" or not source_identifier:
            return
        key = source_identifier.strip()
        if not key.startswith(f"{job_id}_"):
            return
        await delete_key(file_key=key)

    @staticmethod
    async def purge_for_user(user_id: str) -> Dict[str, int]:
        """
        Remove detail older than this user's effective retention window.
        Keeps extraction_results and migration_batches summary rows; clears row-level tables and S3 file uploads.
        """
        # Compare timestamps in PostgreSQL only (NOW() - interval) so we never bind a Python
        # datetime that can trip asyncpg/databases (naive vs aware) across drivers and column types.
        effective_days = await DataRetentionService.get_effective_retention_days(user_id)

        stats = {
            "extraction_jobs_touched": 0,
            "s3_deletes_attempted": 0,
        }

        extractions = await database.fetch_all(
            """
            SELECT id, job_id, source_type, source_identifier
            FROM extraction_results
            WHERE user_id = CAST(:user_id AS uuid)
              AND created_at < NOW() - (CAST(:effective_days AS INTEGER) * INTERVAL '1 day')
              AND detail_purged_at IS NULL
            """,
            values={"user_id": user_id, "effective_days": effective_days},
        )
        for row in extractions:
            r = dict(row)
            job_id = r["job_id"]

            await database.execute(
                """
                DELETE FROM extraction_result_records
                WHERE job_id = :job_id
                """,
                values={"job_id": job_id},
            )

            await database.execute(
                """
                UPDATE extraction_results
                SET extracted_data = '[]'::jsonb,
                    detail_purged_at = NOW()
                WHERE id = :id
                """,
                values={"id": r["id"]},
            )

            await DataRetentionService._delete_s3_key_if_file_source(
                job_id, r.get("source_type"), r.get("source_identifier")
            )
            if (r.get("source_type") or "").lower() == "file" and r.get("source_identifier"):
                stats["s3_deletes_attempted"] += 1

            stats["extraction_jobs_touched"] += 1

        await database.execute(
            """
            DELETE FROM migration_records mr
            USING migration_batches mb
            WHERE mr.batch_id = mb.id
              AND mb.user_id = CAST(:user_id AS uuid)
              AND mb.created_at < NOW() - (CAST(:effective_days AS INTEGER) * INTERVAL '1 day')
              AND mb.detail_purged_at IS NULL
            """,
            values={"user_id": user_id, "effective_days": effective_days},
        )

        await database.execute(
            """
            UPDATE migration_batches
            SET detail_purged_at = NOW()
            WHERE user_id = CAST(:user_id AS uuid)
              AND created_at < NOW() - (CAST(:effective_days AS INTEGER) * INTERVAL '1 day')
              AND detail_purged_at IS NULL
            """,
            values={"user_id": user_id, "effective_days": effective_days},
        )

        return stats

    @staticmethod
    async def purge_all_users() -> Dict[str, Any]:
        rows = await database.fetch_all("SELECT id FROM users")
        total = {"users": 0, "extraction_jobs_touched": 0, "s3_deletes_attempted": 0}
        for row in rows:
            uid = str(dict(row)["id"])
            s = await DataRetentionService.purge_for_user(uid)
            total["users"] += 1
            total["extraction_jobs_touched"] += s["extraction_jobs_touched"]
            total["s3_deletes_attempted"] += s["s3_deletes_attempted"]
        return total
