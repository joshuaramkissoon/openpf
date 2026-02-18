from __future__ import annotations

import logging
import os
import signal
import time

from app.core.database import SessionLocal, init_db
from app.services.task_scheduler_service import run_due_tasks, seed_default_tasks
from app.services.telegram_service import process_telegram_updates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
logger = logging.getLogger("worker")


class _Stop:
    value = False


def _handle_stop(signum, frame):  # type: ignore[no-untyped-def]
    _Stop.value = True


def main() -> None:
    poll_seconds = max(2, int(os.environ.get("WORKER_POLL_SECONDS", "15")))

    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    init_db()
    logger.info("worker started; poll interval=%ss", poll_seconds)

    while not _Stop.value:
        started = time.time()
        db = SessionLocal()
        try:
            seed_default_tasks(db)
            task_results = run_due_tasks(db)
            processed_updates = process_telegram_updates(db)
            if task_results:
                logger.info("ran %d due tasks", len(task_results))
            if processed_updates:
                logger.info("processed %d telegram updates", processed_updates)
        except Exception as exc:  # noqa: BLE001
            logger.exception("worker loop failed: %s", exc)
        finally:
            db.close()

        elapsed = time.time() - started
        sleep_for = max(0.5, poll_seconds - elapsed)
        time.sleep(sleep_for)

    logger.info("worker stopped")


if __name__ == "__main__":
    main()
