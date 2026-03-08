import asyncio
import logging
from typing import Any
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_APSCHEDULER = True
except ImportError:
    HAS_APSCHEDULER = False

from report_engine import ReportEngine

logger = logging.getLogger(__name__)

class ReportScheduler:
    def __init__(self, client_manager: Any, report_engine: ReportEngine):
        self.client_manager = client_manager
        self.report_engine = report_engine
        self.scheduler = None

    def start(self):
        if not HAS_APSCHEDULER:
            logger.warning("apscheduler not installed. ReportScheduler will not run background jobs.")
            return

        if self.scheduler is None:
            self.scheduler = AsyncIOScheduler()

        # Schedule daily digest (8 PM UTC)
        self.scheduler.add_job(
            self._run_daily_digest,
            CronTrigger(hour=20, minute=0, timezone="UTC"),
            id="daily_digest",
            replace_existing=True
        )
        
        # Schedule weekly report (Monday 9 AM UTC)
        self.scheduler.add_job(
            self._run_weekly_report,
            CronTrigger(day_of_week="mon", hour=9, minute=0, timezone="UTC"),
            id="weekly_report",
            replace_existing=True
        )
        self.scheduler.start()
        logger.info("ReportScheduler started.")

    async def _run_daily_digest(self):
        clients = self.client_manager.list_clients()
        for cid in clients:
            try:
                config = self.client_manager.load_client(cid)
                await self.report_engine.send_daily_digest(cid, config)
            except Exception as e:
                logger.error(f"Error sending daily digest for {cid}: {e}")

    async def _run_weekly_report(self):
        clients = self.client_manager.list_clients()
        for cid in clients:
            try:
                config = self.client_manager.load_client(cid)
                await self.report_engine.send_weekly_report(cid, config)
            except Exception as e:
                logger.error(f"Error sending weekly report for {cid}: {e}")

    def stop(self):
        if self.scheduler:
            self.scheduler.shutdown()
            logger.info("ReportScheduler stopped.")
