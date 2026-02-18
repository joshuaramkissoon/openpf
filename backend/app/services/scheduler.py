from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.database import SessionLocal
from app.services.task_scheduler_service import run_due_tasks, seed_default_tasks
from app.services.telegram_service import process_telegram_updates

logger = logging.getLogger(__name__)

scheduler: BackgroundScheduler | None = None


def _worker_tick() -> None:
    db = SessionLocal()
    try:
        seed_default_tasks(db)
        run_due_tasks(db)
        process_telegram_updates(db)
    except Exception as exc:
        logger.exception("scheduler worker tick failed: %s", exc)
    finally:
        db.close()


def start_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(_worker_tick, "interval", seconds=15, id="worker-tick", max_instances=1, coalesce=True)
    scheduler.start()
    logger.info("Background scheduler started")


def stop_scheduler() -> None:
    global scheduler
    if scheduler is None:
        return
    scheduler.shutdown(wait=False)
    scheduler = None
    logger.info("Background scheduler stopped")
