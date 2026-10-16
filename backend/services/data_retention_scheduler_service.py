"""
Runs data retention purge on an interval (default: daily).
"""

import traceback
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger


class DataRetentionSchedulerService:
    def __init__(self, interval_hours: float = 24.0):
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
        self.interval_hours = interval_hours

    async def run_purge(self) -> None:
        try:
            from services.data_retention_service import DataRetentionService

            summary = await DataRetentionService.purge_all_users()
            print(f"✅ data_retention purge_all_users: {summary}")
        except Exception as e:
            print(f"❌ data_retention purge failed: {e}")
            traceback.print_exc()

    def start(self) -> None:
        if self.is_running:
            return
        self.scheduler.add_job(
            self.run_purge,
            trigger=IntervalTrigger(hours=self.interval_hours),
            id="data_retention_purge",
            name="Purge extraction/migration detail past retention",
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


data_retention_scheduler_service: Optional[DataRetentionSchedulerService] = None


def get_data_retention_scheduler() -> DataRetentionSchedulerService:
    global data_retention_scheduler_service
    if data_retention_scheduler_service is None:
        data_retention_scheduler_service = DataRetentionSchedulerService()
    return data_retention_scheduler_service
