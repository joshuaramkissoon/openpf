from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, broker, charts, config, costs, health, leveraged, portfolio, scheduler, strategy, telegram, theses
from app.core.config import get_settings
from app.core.database import init_db
from app.services.claude_chat_runtime import claude_chat_runtime
from app.services.scheduler import start_scheduler, stop_scheduler

settings = get_settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.inproc_scheduler_enabled:
        start_scheduler()
    yield
    await claude_chat_runtime.shutdown()
    if settings.inproc_scheduler_enabled:
        stop_scheduler()


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
app.include_router(leveraged.router, prefix=settings.api_prefix)
app.include_router(scheduler.router, prefix=settings.api_prefix)
app.include_router(strategy.router, prefix=settings.api_prefix)
app.include_router(telegram.router, prefix=settings.api_prefix)
app.include_router(theses.router, prefix=settings.api_prefix)
app.include_router(charts.router, prefix=settings.api_prefix)
app.include_router(costs.router, prefix=settings.api_prefix)
