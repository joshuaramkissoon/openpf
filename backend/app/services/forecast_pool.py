"""Run Kronos forecasts in an isolated worker process.

Why this exists
---------------
`kronos_service.forecast()` loads torch and runs the Kronos model. On Apple
Silicon it dispatches to the Metal/MPS GPU. When that runs **in the web
process on a threadpool worker thread** (which is what a sync FastAPI
endpoint does), a fault in Apple's Metal driver is a `SIGSEGV` that kills the
whole backend — no Python traceback, no graceful 500 (confirmed via a crash
report: thread "metal gpu stream", `clamp_scalar_kernel_mps` →
`AGXMetal…setComputePipelineState:`).

Running the forecast in a dedicated child process fixes this two ways:

* the model executes on the **child's main thread**, avoiding the
  off-main-thread MPS crash class entirely; and
* if the child still dies (segfault/OOM), it takes down **only the child** —
  the web server stays up, the request returns a clean 503, and the pool
  transparently spins up a fresh worker for the next request.

The single long-lived worker (``max_workers=1``) keeps the model resident
across requests, so only the first call pays the load cost. The `forecast`
MCP server already runs in its own subprocess and is unaffected by this.
"""
from __future__ import annotations

import asyncio
import functools
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable

# Generous default: the first call may download model weights. Subsequent
# calls reuse the resident model and are far quicker.
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("FORECAST_WORKER_TIMEOUT", "180"))

# 'spawn' (not 'fork') is required: forking a process that has already touched
# torch/Metal/threads is itself a crash source on macOS. spawn gives a clean
# interpreter that loads torch fresh on its own main thread.
_MP_CONTEXT = multiprocessing.get_context("spawn")

_pool: ProcessPoolExecutor | None = None
# Guards pool creation/replacement. A plain threading.Lock (not asyncio.Lock)
# so it is not bound to a specific event loop.
_pool_lock = threading.Lock()


class ForecastWorkerError(RuntimeError):
    """The forecast worker process died (crash/OOM) before returning."""


class ForecastTimeout(RuntimeError):
    """The forecast worker exceeded its time budget."""


def _get_pool() -> ProcessPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1, mp_context=_MP_CONTEXT)
        return _pool


def _discard_pool() -> None:
    """Drop the current pool so the next call builds a fresh worker.

    Used after a hard crash or a timeout — a broken pool can never be reused,
    and a timed-out worker may be wedged, so we replace it wholesale.
    """
    global _pool
    with _pool_lock:
        dead, _pool = _pool, None
    if dead is not None:
        # Don't wait: a wedged worker would block us. cancel_futures clears the
        # queue; the orphaned worker exits on its own.
        dead.shutdown(wait=False, cancel_futures=True)


async def submit(fn: Callable[..., Any], *, timeout: float = DEFAULT_TIMEOUT_SECONDS, **kwargs: Any) -> Any:
    """Run ``fn(**kwargs)`` in the isolated worker and await its result.

    Raises ``ForecastTimeout`` if it exceeds ``timeout``, ``ForecastWorkerError``
    if the worker crashes, and re-raises any ordinary exception ``fn`` raised
    (e.g. ForecastError/ForecastUnavailableError) unchanged. The pool recovers
    after a crash or timeout so the next call succeeds.
    """
    loop = asyncio.get_running_loop()
    pool = _get_pool()
    call = functools.partial(fn, **kwargs)
    try:
        return await asyncio.wait_for(loop.run_in_executor(pool, call), timeout=timeout)
    except asyncio.TimeoutError as exc:
        _discard_pool()
        raise ForecastTimeout(f"forecast timed out after {timeout:.0f}s") from exc
    except BrokenProcessPool as exc:
        _discard_pool()
        raise ForecastWorkerError("forecast worker crashed") from exc


async def run_forecast(**kwargs: Any) -> dict:
    """Run ``kronos_service.forecast`` in the isolated worker process."""
    # Imported lazily and *by reference* so the spawned worker re-imports it
    # (and torch) in its own clean interpreter.
    from app.services.kronos_service import forecast as _forecast

    return await submit(_forecast, **kwargs)


def shutdown() -> None:
    """Tear down the worker pool (app shutdown / test teardown)."""
    _discard_pool()


# ── self-test helpers ───────────────────────────────────────────────────────
# Top-level, picklable functions that the test-suite submits to the real pool
# to exercise crash isolation. They live here (not in the test module) so the
# spawned worker can import them by qualified name.

def _selftest_echo(value: Any) -> Any:
    return value


def _selftest_crash(mode: str = "exit") -> None:
    if mode == "raise":
        raise ValueError("boom")
    os._exit(1)  # hard death, like a native SIGSEGV


def _selftest_sleep(seconds: float) -> str:
    time.sleep(seconds)
    return "slept"
