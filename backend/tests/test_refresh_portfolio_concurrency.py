"""Concurrency / latency behaviour of refresh_portfolio.

The dashboard fires POST /portfolio/refresh on load and *blocks* the render on
it. So refresh must never queue behind an in-flight refresh that is doing slow
T212 / cashflow network I/O — a force=False (request-path) refresh returns the
latest cached snapshot immediately instead of waiting on the lock. The cashflow
sync must also run OUTSIDE the refresh lock so it can't stall other refreshes.
"""

import threading
from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models.entities import AccountSnapshot
from app.services import portfolio_service as ps


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(
        AccountSnapshot(
            fetched_at=datetime.utcnow(), account_kind="invest", currency="GBP",
            free_cash=1.0, invested=2.0, pie_cash=0.0, total=3.0, ppl=0.0,
        )
    )
    session.commit()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def _reset_refresh_state():
    ps._last_refresh_ts = None
    while ps._refresh_lock.locked():
        try:
            ps._refresh_lock.release()
        except RuntimeError:
            break
    yield
    ps._last_refresh_ts = None
    while ps._refresh_lock.locked():
        try:
            ps._refresh_lock.release()
        except RuntimeError:
            break


def _call_with_timeout(fn, timeout=3.0):
    box: dict = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise AssertionError(
            f"refresh_portfolio blocked (no return within {timeout:.1f}s)"
        )
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_cooldown_returns_without_waiting_on_lock(db):
    # A refresh happened within the cooldown window AND another refresh is
    # in-flight (lock held). A force=False request must serve cache immediately,
    # never touching the lock.
    ps._last_refresh_ts = datetime.utcnow()
    ps._refresh_lock.acquire()  # simulate an in-flight refresh
    try:
        result = _call_with_timeout(lambda: ps.refresh_portfolio(db, force=False))
    finally:
        ps._refresh_lock.release()
    assert result["source"] == "cooldown-cache"


def test_request_path_does_not_block_on_held_lock(db):
    # Cooldown expired, but a refresh is in-flight (lock held). A force=False
    # request must return the latest snapshot immediately, not queue behind it.
    ps._last_refresh_ts = datetime.utcnow() - timedelta(seconds=3600)
    ps._refresh_lock.acquire()
    try:
        result = _call_with_timeout(lambda: ps.refresh_portfolio(db, force=False))
    finally:
        ps._refresh_lock.release()
    assert result["source"] == "refresh-in-progress"


def test_cashflow_sync_runs_outside_refresh_lock(db, monkeypatch):
    # maybe_sync_all can page T212 with backoff; it must NOT run while the refresh
    # lock is held, or it stalls every other refresh.
    monkeypatch.setattr(
        "app.services.config_store.ConfigStore.enabled_account_kinds",
        lambda self: [],
    )
    seen: dict = {}
    import app.services.cashflow_service as cf

    def fake_sync(_db):
        seen["lock_held_during_sync"] = ps._refresh_lock.locked()
        return None

    monkeypatch.setattr(cf, "maybe_sync_all", fake_sync)

    ps.refresh_portfolio(db, force=True)

    assert seen.get("lock_held_during_sync") is False
