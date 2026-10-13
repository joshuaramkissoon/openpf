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


def _plays_review_tick() -> None:
    """Daily deterministic check of active Plays (kill rules + drift → alerts)."""
    from app.services.plays_service import review_active_plays

    db = SessionLocal()
    try:
        result = review_active_plays(db)
        logger.info("plays review: %s", result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("plays review tick failed: %s", exc)
    finally:
        db.close()


def _track_record_tick() -> None:
    """Daily: mark proposal outcomes (1d/1w/1m/3m/close vs benchmark), then let
    the record suggest standing permissions where it's earned them. Suggestions
    grant nothing — the user decides."""
    from app.services.permissions_service import generate_suggestions
    from app.services.track_record_service import mark_outcomes

    db = SessionLocal()
    try:
        counts = mark_outcomes(db)
        suggested = generate_suggestions(db)
        logger.info("track record marked: %s; suggestions: %d", counts, len(suggested))
    except Exception as exc:  # noqa: BLE001
        logger.exception("track record tick failed: %s", exc)
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
    scheduler.add_job(
        _plays_review_tick, "cron", hour=21, minute=15, id="daily-plays-review",
        max_instances=1, coalesce=True, misfire_grace_time=6 * 3600,
    )
    scheduler.add_job(
        _track_record_tick, "cron", hour=21, minute=30, id="daily-track-record",
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
