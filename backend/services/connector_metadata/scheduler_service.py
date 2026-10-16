"""Scheduled connector schema sync (Connector Metadata Agent)."""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime
from typing import Any, Dict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from services.connector_metadata import store
from services.connector_metadata.config import DEFAULT_SCHEMA_SYNC_CONCURRENCY
from services.connector_metadata.job_worker import start_metadata_sync_job
from services.schedule_calculator import calculate_next_run

logger = logging.getLogger(__name__)


class ConnectorMetadataSchedulerService:
    def __init__(self, max_concurrent: int = DEFAULT_SCHEMA_SYNC_CONCURRENCY) -> None:
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_scheduled_sync(self, schedule_row: Dict[str, Any]) -> None:
        async with self._semaphore:
            schedule_id = str(schedule_row["id"])
            user_id = str(schedule_row["user_id"])
            connector_type = schedule_row["connector_type"]
            connector_ref = schedule_row.get("connector_ref")
            if isinstance(connector_ref, str):
                connector_ref = json.loads(connector_ref)
            connector_ref = connector_ref or {}

            active = await store.get_active_job(user_id, connector_type, connector_ref)
            if active:
                logger.info("Skipping scheduled sync; job already active %s", active.get("id"))
                return

            try:
                from db.connection import database

                await database.execute(
                    """
                    UPDATE connector_schema_schedules
                    SET last_run_at = NOW(), updated_at = NOW()
                    WHERE id = :id
                    """,
                    {"id": schedule_id},
                )

                job_id = await store.create_job(
                    user_id=user_id,
                    connector_type=connector_type,
                    connector_ref=connector_ref,
                    sync_mode=schedule_row.get("sync_mode") or "schema_and_categorize",
                    scope="full",
                    options={
                        "build_graph": True,
                        "graph_scope": schedule_row.get("graph_scope") or "neighborhood",
                        "trigger": "schedule",
                    },
                )

                if schedule_row.get("schedule_enabled") and schedule_row.get("schedule_frequency"):
                    tz = schedule_row.get("schedule_timezone") or "UTC"
                    cfg = schedule_row.get("schedule_config")
                    if isinstance(cfg, str):
                        cfg = json.loads(cfg)
                    next_run = calculate_next_run(
                        frequency=schedule_row["schedule_frequency"],
                        schedule_config=cfg or {},
                        timezone=tz,
                        current_time=datetime.utcnow(),
                    )
                    await database.execute(
                        """
                        UPDATE connector_schema_schedules
                        SET next_run_at = :next_run_at, last_job_id = :job_id, updated_at = NOW()
                        WHERE id = :id
                        """,
                        {"id": schedule_id, "next_run_at": next_run, "job_id": job_id},
                    )

                await start_metadata_sync_job(job_id, user_id)
            except Exception as exc:
                logger.exception("Scheduled metadata sync failed schedule=%s", schedule_id)
                from db.connection import database

                await database.execute(
                    """
                    UPDATE connector_schema_schedules
                    SET last_error = :err, updated_at = NOW()
                    WHERE id = :id
                    """,
                    {"id": schedule_id, "err": str(exc)[:500]},
                )

    async def check_and_run_due_schedules(self) -> None:
        try:
            due = await store.list_due_schedules()
            for row in due:
                asyncio.create_task(self._run_scheduled_sync(row))
        except Exception:
            logger.exception("Error checking metadata schedules")
            traceback.print_exc()

    def start(self) -> None:
        if self.is_running:
            return
        self.scheduler.add_job(
            self.check_and_run_due_schedules,
            trigger=IntervalTrigger(minutes=1),
            id="check_connector_schema_schedules",
            name="Check connector schema sync schedules",
            replace_existing=True,
            max_instances=1,
        )
        self.scheduler.start()
        self.is_running = True

    def stop(self) -> None:
        if not self.is_running:
            return
        self.scheduler.shutdown()
        self.is_running = False


connector_metadata_scheduler_service = ConnectorMetadataSchedulerService()
