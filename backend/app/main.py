from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, attention, broker, charts, config, costs, health, instruments, leveraged, orders, portfolio, research, scheduler, strategy, telegram, theses, watchlist
from app.core.config import get_settings
from app.core.database import init_db
from app.services.claude_chat_runtime import claude_chat_runtime
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


def _scheduler_should_run() -> bool:
    """Scheduler runs if the env flag OR the in-app toggle (DB) says so — so it
    can be controlled from Settings without touching .env."""
    if settings.inproc_scheduler_enabled:
        return True
    try:
        from app.core.database import SessionLocal
        from app.services.config_store import ConfigStore

        with SessionLocal() as db:
            return bool(ConfigStore(db).get_broker().get("scheduler_enabled"))
    except Exception:  # noqa: BLE001
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # One-time migration off the legacy config symbol list onto the watchlist table.
    try:
        from app.core.database import SessionLocal
        from app.services import watchlist_service

        with SessionLocal() as db:
            seeded = watchlist_service.seed_from_config_if_empty(db)
            if seeded:
                logging.getLogger(__name__).info("Seeded %d watchlist items from config", seeded)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("Watchlist seed skipped", exc_info=True)
    if _scheduler_should_run():
        start_scheduler()
    yield
    await claude_chat_runtime.shutdown()
    stop_scheduler()
    from app.services.forecast_pool import shutdown as shutdown_forecast_pool

    shutdown_forecast_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_origin_regex=settings.cors_allow_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(config.router, prefix=settings.api_prefix)
app.include_router(broker.router, prefix=settings.api_prefix)
app.include_router(portfolio.router, prefix=settings.api_prefix)
app.include_router(agent.router, prefix=settings.api_prefix)
app.include_router(orders.router, prefix=settings.api_prefix)
app.include_router(leveraged.router, prefix=settings.api_prefix)
app.include_router(scheduler.router, prefix=settings.api_prefix)
app.include_router(strategy.router, prefix=settings.api_prefix)
app.include_router(telegram.router, prefix=settings.api_prefix)
app.include_router(theses.router, prefix=settings.api_prefix)
app.include_router(charts.router, prefix=settings.api_prefix)
app.include_router(costs.router, prefix=settings.api_prefix)
app.include_router(research.router, prefix=settings.api_prefix)
app.include_router(attention.router, prefix=settings.api_prefix)
app.include_router(watchlist.router, prefix=settings.api_prefix)
app.include_router(instruments.router, prefix=settings.api_prefix)
