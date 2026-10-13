from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import plays as plays_api
from app.api import agent, attention, broker, charts, config, costs, health, instruments, leveraged, orders, portfolio, research, scheduler, strategy, telegram, theses, watchlist
from app.core.auth import ApiTokenMiddleware, OriginGuardMiddleware

from app.api import recipes as recipes_api
from app.api import views
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


def _warm_sandbox_history() -> None:
    """First sandbox boot: pull the mock deposits and rebuild the equity curve from
    the mock order history, so the demo opens with a full chart, not an empty one."""
    import threading

    def _run() -> None:
        from app.core.database import SessionLocal
        from app.services.cashflow_service import sync_all
        from app.services.equity_backfill import backfill_all

        try:
            with SessionLocal() as db:
                sync_all(db)
                backfill_all(db)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("Sandbox history warm-up failed", exc_info=True)

    threading.Thread(target=_run, name="sandbox-warmup", daemon=True).start()


def _sandbox_enabled() -> bool:
    # Keyed off the process env (OPENPF_SANDBOX=1 or T212_BASE_ENV=sandbox) so
    # the isolation in core.config/core.sandbox always applies with it.
    from app.core.sandbox import is_sandbox

    return is_sandbox()


def _start_scheduler_safely() -> bool:
    """Start the in-process scheduler if enabled. A failure is logged, never
    fatal — the dashboard must still come up."""
    if not _scheduler_should_run():
        return False
    try:
        start_scheduler()
        return True
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).error("Scheduler failed to start; continuing without it", exc_info=True)
        return False


def _bind_host() -> str:
    """Best-effort host uvicorn was bound to (``--host`` arg or UVICORN_HOST)."""
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--host="):
            return arg.split("=", 1)[1]
    return os.environ.get("UVICORN_HOST", "127.0.0.1")


def _warn_if_exposed_without_token() -> None:
    host = _bind_host()
    if settings.openpf_api_token or host in {"127.0.0.1", "localhost", "::1"}:
        return
    logging.getLogger(__name__).warning(
        "API is bound to %s with no OPENPF_API_TOKEN set — anyone who can reach this host can "
        "read your portfolio and approve trades. Set OPENPF_API_TOKEN or bind to 127.0.0.1.",
        host,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_if_exposed_without_token()
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
    # Built-in recipes (and adoption of the legacy brief/review tasks) + the
    # recipe skills plugin Archie loads.
    try:
        from app.core.database import SessionLocal
        from app.services import recipes_service

        with SessionLocal() as db:
            recipes_service.ensure_builtins(db)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("Recipe seed skipped", exc_info=True)
    if _sandbox_enabled():
        try:
            from app.core.database import SessionLocal
            from app.services import sandbox_broker

            with SessionLocal() as db:
                if sandbox_broker.seed_config_if_fresh(db):
                    logging.getLogger(__name__).info("Sandbox broker enabled (no real money): %s", sandbox_broker.sandbox_base_url())
                    _warm_sandbox_history()
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).warning("Sandbox seed skipped", exc_info=True)
        # Fail closed: a sandbox that could reach live data/keys must not start.
        from app.core.sandbox import SandboxViolation, verify_isolation

        log = logging.getLogger("openpf.sandbox")
        try:
            lines = verify_isolation()
        except SandboxViolation as exc:
            log.critical("SANDBOX ISOLATION FAILED — refusing to start: %s", exc)
            raise
        banner = "=" * 72
        log.warning("\n%s\n  SANDBOX MODE — mock broker, fake money, isolated data\n%s\n%s", banner,
                    "\n".join(f"  {line}" for line in lines), banner)
    # Nothing can be mid-order before we've served a request: anything still
    # 'executing' was interrupted by a crash/restart — surface it, don't spin.
    try:
        from app.core.database import SessionLocal
        from app.services.execution_service import recover_stuck_executing

        with SessionLocal() as db:
            recover_stuck_executing(db, older_than_minutes=0)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).warning("Stuck-intent recovery skipped", exc_info=True)
    _start_scheduler_safely()
    # Builds Archie queues for agent-built Views (see views_builder.enqueue).
    import asyncio

    from app.services import views_builder

    views_queue = asyncio.create_task(views_builder.queue_worker())
    yield
    views_queue.cancel()
    await claude_chat_runtime.shutdown()
    stop_scheduler()
    from app.services.forecast_pool import shutdown as shutdown_forecast_pool

    shutdown_forecast_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def _unhandled_error(request, exc):  # noqa: ANN001
    """Last-resort 500: log the traceback server-side, return a JSON message the
    UI can show — never the exception text or a stack trace."""
    from fastapi.responses import JSONResponse

    logging.getLogger("openpf.api").exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal error — see the OpenPF server log for details."})

# Added before CORS so CORS stays outermost and 401s still carry CORS headers.
app.add_middleware(
    OriginGuardMiddleware,
    allowed_origins=settings.cors_origins_list,
    allowed_regex=settings.cors_allow_origin_regex or None,
    api_prefix=settings.api_prefix,
)
if settings.openpf_api_token:
    app.add_middleware(ApiTokenMiddleware, token=settings.openpf_api_token, api_prefix=settings.api_prefix)

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
app.include_router(plays_api.router, prefix=settings.api_prefix)
app.include_router(views.router, prefix=settings.api_prefix)
app.include_router(recipes_api.router, prefix=settings.api_prefix)

if _sandbox_enabled():
    from app.api import sandbox_t212

    app.include_router(sandbox_t212.router)
