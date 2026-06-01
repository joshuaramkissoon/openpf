"""Tests for the isolated forecast process pool.

These use *real* subprocesses (spawn) so they genuinely exercise crash
isolation — the whole point of the pool is that a hard worker crash
(SIGSEGV from torch/MPS, OOM, etc.) takes down only the child, never the
web process, and the pool transparently recovers for the next request.
"""
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import asyncio

from app.services.forecast_pool import (
    ForecastTimeout,
    ForecastWorkerError,
    shutdown,
    submit,
    _selftest_crash,
    _selftest_echo,
    _selftest_sleep,
)


@pytest.fixture(autouse=True)
def _reset_pool():
    shutdown()
    yield
    shutdown()


def test_submit_runs_in_worker_and_returns_result():
    assert asyncio.run(submit(_selftest_echo, value=41)) == 41


def test_normal_worker_exception_propagates():
    # A regular Python exception in the worker (e.g. ForecastError for bad
    # input) must propagate unchanged so the endpoint can map it to 4xx.
    with pytest.raises(ValueError, match="boom"):
        asyncio.run(submit(_selftest_crash, mode="raise"))


def test_worker_hard_crash_is_isolated_and_pool_recovers():
    async def scenario():
        # os._exit(1) inside the worker == a SIGSEGV-style hard death.
        with pytest.raises(ForecastWorkerError):
            await submit(_selftest_crash, mode="exit")
        # The pool must recover: the very next call succeeds on a fresh worker.
        return await submit(_selftest_echo, value=7)

    assert asyncio.run(scenario()) == 7


def test_timeout_raises_and_pool_recovers():
    async def scenario():
        with pytest.raises(ForecastTimeout):
            await submit(_selftest_sleep, seconds=10, timeout=0.3)
        # A stuck worker must not wedge the pool for subsequent requests.
        return await submit(_selftest_echo, value=9)

    assert asyncio.run(scenario()) == 9
