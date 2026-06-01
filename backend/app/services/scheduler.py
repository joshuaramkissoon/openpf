from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.database import SessionLocal
from app.services.portfolio_service import refresh_portfolio
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


def _daily_snapshot_tick() -> None:
    """Record a portfolio snapshot daily, independent of the dashboard.

    The equity curve is built from AccountSnapshot rows, which were previously
    only written when the dashboard was open (auto-refresh + poll) — leaving
    multi-month gaps whenever nobody had the tab open. A server-side daily
    snapshot keeps the curve dense going forward."""
    db = SessionLocal()
    try:
        result = refresh_portfolio(db, force=True)
        logger.info("daily portfolio snapshot recorded: %s", result.get("source"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("daily snapshot tick failed: %s", exc)
    finally:
        db.close()


def start_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        return

    scheduler = BackgroundScheduler()
    scheduler.add_job(_worker_tick, "interval", seconds=15, id="worker-tick", max_instances=1, coalesce=True)
    # Daily equity-curve snapshot. Generous misfire grace + coalesce so a server
    # that wasn't up at exactly 21:00 still records once when it next runs.
    scheduler.add_job(
        _daily_snapshot_tick, "cron", hour=21, minute=0, id="daily-portfolio-snapshot",
        max_instances=1, coalesce=True, misfire_grace_time=6 * 3600,
    )
    scheduler.start()
    logger.info("Background scheduler started")


def stop_scheduler() -> None:
    global scheduler
    if scheduler is None:
        return
    scheduler.shutdown(wait=False)
    scheduler = None
    logger.info("Background scheduler stopped")
