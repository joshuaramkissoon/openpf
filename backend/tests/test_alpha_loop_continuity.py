"""Tests for the alpha-loop continuity / correctness work.

Covers:
* A1 — `_build_goal_context` injects the LEVERAGED-only realized P&L (engine
  trades), framed so the agent does not recount it account-wide.
* A3 — multi-day holds: `_days_held` and `_should_force_close_for_age`.
* A4 — blocked-run detection (`_detect_block`/`_is_transient_error`) and the
  auto-retry scheduling in `_touch_task_after_run`.
* B  — held-position service: classify+merge, full/partial/external close, adopt.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import LeveragedTrade, ScheduledTask
from app.services import leveraged_service as ls
from app.services import task_scheduler_service as ts

UK = ZoneInfo("Europe/London")


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        # These tests exercise the engine's close/P&L logic, not live execution —
        # pin paper mode (the app default is now 'live').
        ls.ConfigStore(s).set_broker({"broker_mode": "paper"})
        yield s


def _utcnow_naive() -> datetime:
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


def _closed_trade(db: Session, *, code: str, pnl: float, exited: datetime | None = None) -> LeveragedTrade:
    now = exited or _utcnow_naive()
    t = LeveragedTrade(
        status="closed",
        symbol=code,
        instrument_code=code,
        account_kind="stocks_isa",
        direction="long",
        quantity=1.0,
        entry_price=100.0,
        entry_notional=100.0,
        entered_at=now,
        exit_price=100.0 + pnl,
        exit_notional=100.0 + pnl,
        exited_at=now,
        close_reason="manual",
        pnl_value=pnl,
        pnl_pct=pnl / 100.0,
        meta={},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _open_trade(db: Session, *, code: str, qty: float, entry: float, entered: datetime | None = None) -> LeveragedTrade:
    t = LeveragedTrade(
        status="open",
        symbol=code,
        instrument_code=code,
        account_kind="stocks_isa",
        direction="long",
        quantity=qty,
        entry_price=entry,
        entry_notional=qty * entry,
        entered_at=entered or _utcnow_naive(),
        stop_loss_pct=0.05,
        take_profit_pct=0.4,
        meta={"source": "auto"},
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


class _FakeT212:
    def __init__(self, positions):
        self._positions = positions
        self.orders: list[tuple[str, float]] = []

    def get_positions(self):
        return list(self._positions)

    def place_market_order(self, code, quantity, **_kw):
        self.orders.append((code, quantity))
        return {"id": "ord-1", "price": 0.0}


def _patch_t212(monkeypatch, positions, name_map):
    monkeypatch.setattr(ls, "_instrument_name_map", lambda _db: name_map)
    monkeypatch.setattr(ls, "build_t212_client", lambda *a, **k: _FakeT212(positions))
    monkeypatch.setattr(ls.ConfigStore, "enabled_account_kinds", lambda self: ["stocks_isa"])


_SNDK_NAME = "Leverage Shares 3x Long SanDisk SNDK (Acc)"


# ── A1 — leveraged-only P&L in goal context ─────────────────────────────────


def test_goal_context_injects_leveraged_only_realized(db, monkeypatch):
    # Two closed leveraged trades today → engine realized = £35.
    _closed_trade(db, code="3SNDl_EQ", pnl=20.0)
    _closed_trade(db, code="3PLTl_EQ", pnl=15.0)

    # Keep the network-dependent enrichers quiet/deterministic.
    monkeypatch.setattr(ts, "list_held_leveraged_positions", lambda _db: [])
    monkeypatch.setattr(ts, "read_recent_daily_goal_lines", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.regime_service.compute_regime", lambda: (_ for _ in ()).throw(RuntimeError("no net")))
    monkeypatch.setattr("app.services.macro_calendar.macro_context_line", lambda: "")
    monkeypatch.setattr("app.services.leveraged_universe.build_universe", lambda *a, **k: {"ranked": []})

    task = ScheduledTask(
        name="alpha_loop_open",
        cron_expr="45 7 * * 1-5",
        timezone="Europe/London",
        meta={"task_kind": "claude_with_goal", "goal": {"target_gbp": 50.0, "loss_limit_gbp": 75.0, "max_trades": 4}},
    )

    ctx = ts._build_goal_context(db, task)

    assert "LEVERAGED realized P&L today = £35.00" in ctx
    assert "core-equity P&L does NOT count" in ctx
    assert "Do NOT re-derive it account-wide" in ctx


# ── A3 — multi-day holds ─────────────────────────────────────────────────────


def test_days_held_counts_uk_calendar_days():
    entered = datetime(2026, 6, 2, 10, 0, 0)  # naive UTC
    now_uk = datetime(2026, 6, 5, 12, 0, 0, tzinfo=UK)
    assert ls._days_held(entered, now_uk) == 3
    assert ls._days_held(None, now_uk) == 0


@pytest.mark.parametrize(
    "allow_overnight,max_days,days_ago,expected",
    [
        (True, 3, 3, True),   # at the cap → close
        (True, 3, 2, False),  # under the cap → hold
        (True, 0, 9, False),  # cap disabled → never age-close
        (False, 3, 9, False), # same-day mode → age rule inert
    ],
)
def test_force_close_for_age(allow_overnight, max_days, days_ago, expected):
    now_uk = datetime(2026, 6, 10, 12, 0, 0, tzinfo=UK)
    entered = (now_uk - timedelta(days=days_ago)).astimezone(timezone.utc).replace(tzinfo=None)
    trade = SimpleNamespace(entered_at=entered)
    policy = {"allow_overnight": allow_overnight, "max_hold_days": max_days}
    assert ls._should_force_close_for_age(trade, policy, now_uk) is expected


def test_policy_normalizes_max_hold_days(db):
    pol = ls.get_policy(db)
    assert pol["max_hold_days"] == 3
    assert pol["allow_overnight"] is True
    updated = ls.update_policy(db, {"max_hold_days": 5})
    assert updated["max_hold_days"] == 5


# ── A4 — blocked-run detection + retry ───────────────────────────────────────


def test_detect_block_from_json_status():
    parsed = {"status": "blocked_pending_state_verification", "blocker": "t212 offline"}
    assert ts._detect_block("report", parsed) == "t212 offline"


def test_detect_block_from_text_marker():
    out = "## Blocker\nThe Trading 212 MCP server is offline this session."
    assert ts._detect_block(out, None) is not None


def test_detect_block_none_for_clean_run():
    assert ts._detect_block("Proposed AMD long, target intact.", {"proposals": [{"x": 1}]}) is None


def test_is_transient_error():
    assert ts._is_transient_error("T212 access denied for 'stocks_isa'")
    assert ts._is_transient_error("connection timed out")
    assert not ts._is_transient_error("ValueError: invalid literal")


def _make_task(db: Session) -> ScheduledTask:
    task = ScheduledTask(
        name="alpha_loop_open",
        cron_expr="45 7 * * 1-5",
        timezone="Europe/London",
        enabled=True,
        meta={"task_kind": "claude_with_goal"},
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_blocked_run_schedules_single_retry(db):
    task = _make_task(db)

    ts._touch_task_after_run(db, task, status="error", blocked=True)
    db.refresh(task)
    assert task.meta["retry_count"] == 1
    delay = (task.next_run_at - _utcnow_naive()).total_seconds()
    assert 7 * 60 < delay < 9 * 60  # ~8 min retry
    assert task.failure_count == 1

    # Second consecutive block: retry budget exhausted → fall back to cron.
    ts._touch_task_after_run(db, task, status="error", blocked=True)
    db.refresh(task)
    assert task.meta["retry_count"] == 0
    assert (task.next_run_at - _utcnow_naive()).total_seconds() > 9 * 60

    # A clean run resets the counter and bumps run_count.
    ts._touch_task_after_run(db, task, status="ok")
    db.refresh(task)
    assert task.meta["retry_count"] == 0
    assert task.run_count == 1


# ── B — held-position management ─────────────────────────────────────────────


def test_list_held_classifies_and_filters(db, monkeypatch):
    positions = [
        {"ticker": "3SNDl_EQ", "quantity": 2.0, "averagePrice": 100.0, "currentPrice": 120.0, "ppl": 40.0},
        {"ticker": "AAPL_US_EQ", "quantity": 5.0, "averagePrice": 150.0, "currentPrice": 160.0, "ppl": 50.0},
    ]
    name_map = {"3SNDL_EQ": _SNDK_NAME, "AAPL_US_EQ": "Apple Inc"}
    _patch_t212(monkeypatch, positions, name_map)

    rows = ls.list_held_leveraged_positions(db)
    assert len(rows) == 1  # core equity filtered out
    row = rows[0]
    assert row["instrument_code"] == "3SNDl_EQ"
    assert row["direction"] == "long"
    assert row["quantity"] == 2.0
    assert row["unrealized_pnl_value"] == 40.0
    assert row["tracked"] is False


def test_list_held_marks_engine_tracked(db, monkeypatch):
    positions = [{"ticker": "3SNDl_EQ", "quantity": 2.0, "averagePrice": 100.0, "currentPrice": 120.0, "ppl": 40.0}]
    _patch_t212(monkeypatch, positions, {"3SNDL_EQ": _SNDK_NAME})
    trade = _open_trade(db, code="3SNDl_EQ", qty=2.0, entry=100.0)

    row = ls.list_held_leveraged_positions(db)[0]
    assert row["tracked"] is True
    assert row["trade_id"] == trade.id


def test_close_external_records_realized_pnl(db, monkeypatch):
    positions = [{"ticker": "3SNDl_EQ", "quantity": 2.0, "averagePrice": 100.0, "currentPrice": 120.0, "ppl": 40.0}]
    _patch_t212(monkeypatch, positions, {"3SNDL_EQ": _SNDK_NAME})

    closed = ls.close_position(db, "3SNDl_EQ")  # paper mode → exit at current price
    assert closed.status == "closed"
    assert closed.pnl_value == pytest.approx(40.0)  # (120-100)*2
    assert closed.meta.get("source") == "external"
    # Ties to A1: the daily leveraged realized figure now reflects this close.
    assert ls._daily_realized_pnl(db) == pytest.approx(40.0)


def test_partial_close_engine_trade(db, monkeypatch):
    monkeypatch.setattr(ls, "get_price", lambda _sym: {"price": 110.0})
    trade = _open_trade(db, code="3PLTl_EQ", qty=4.0, entry=100.0)

    sliced = ls.close_position(db, "3PLTl_EQ", quantity=1.0, reason="trim")
    assert sliced.status == "closed"
    assert sliced.quantity == 1.0
    assert sliced.pnl_value == pytest.approx(10.0)  # (110-100)*1
    db.refresh(trade)
    assert trade.status == "open"
    assert trade.quantity == pytest.approx(3.0)


def test_adopt_position_creates_tracked_open_trade(db, monkeypatch):
    positions = [{"ticker": "3SNDl_EQ", "quantity": 2.0, "averagePrice": 100.0, "currentPrice": 120.0, "ppl": 40.0}]
    _patch_t212(monkeypatch, positions, {"3SNDL_EQ": _SNDK_NAME})

    trade = ls.adopt_position(db, "3SNDl_EQ", stop_loss_pct=0.06, take_profit_pct=0.3)
    assert trade.status == "open"
    assert trade.meta["source"] == "adopted"
    assert trade.stop_loss_pct == pytest.approx(0.06)
    assert trade.quantity == 2.0
    # Adopting again is rejected (already tracked).
    with pytest.raises(ls.LeveragedError):
        ls.adopt_position(db, "3SNDl_EQ")
