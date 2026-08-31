import asyncio
import signal

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fb_group_downloader.config import AppConfig
from fb_group_downloader.db import Database
from fb_group_downloader.scraper.coordinator import SyncCoordinator
from fb_group_downloader.utils.logger import console, get_logger

logger = get_logger()


class SchedulerDaemon:
    def __init__(self, config: AppConfig, db: Database):
        self.config = config
        self.db = db
        self.coordinator = SyncCoordinator(config, db)
        self.scheduler = AsyncIOScheduler()
        self._is_running_job = False

    async def _job_wrapper(self) -> None:
        if self._is_running_job:
            logger.warning("前一次同步任務尚未結束，本次排程跳過。")
            return

        self._is_running_job = True
        try:
            logger.info("⏰ 觸發定期同步任務...")
            await self.coordinator.run_all()
        except Exception as e:
            logger.error(f"執行排程同步任務時發生未預期錯誤：{e}", exc_info=True)
        finally:
            self._is_running_job = False

    async def start(self, run_immediately: bool = True) -> None:
        """啟動定時排程服務"""
        sched_cfg = self.config.schedule

        if sched_cfg.cron:
            trigger = CronTrigger.from_crontab(sched_cfg.cron)
            logger.info(f"排程模式：Cron (表達式: '{sched_cfg.cron}')")
        else:
            trigger = IntervalTrigger(minutes=sched_cfg.interval_minutes)
            logger.info(f"排程模式：固定間隔 (每 {sched_cfg.interval_minutes} 分鐘執行一次)")

        self.scheduler.add_job(self._job_wrapper, trigger=trigger, name="fb_group_sync")
        self.scheduler.start()

        console.print("[bold green]✓ Facebook 社團下載排程守護程序已成功啟動！[/bold green]")
        console.print("按 Ctrl+C 可停止服務。\n")

        if run_immediately:
            logger.info("正在執行初始首次同步...")
            await self._job_wrapper()

        # 等待停止信號
        stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except (NotImplementedError, RuntimeError):
                pass

        try:
            await stop_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            logger.info("正在停止排程服務...")
            self.scheduler.shutdown(wait=False)
            logger.info("排程服務已安全關閉。")
