"""Today's move has one definition: Home (/portfolio/today) and the Views data
source's ``day_change`` fields come from the same function and must agree."""

from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest

from app.services import day_move, views_data

CANDLES = {
    "NVDA": [{"date": "2026-10-09", "close": 100.0}, {"date": "2026-10-12", "close": 98.0}],
    "VUSA.L": [{"date": "2026-10-09", "close": 9000.0}, {"date": "2026-10-12", "close": 9090.0}],
    "MSFT": [{"date": "2026-10-09", "close": 500.0}, {"date": "2026-10-12", "close": 505.0}],
}

SNAPSHOT = {
    "account": {"currency": "GBP", "total": 12000.0, "free_cash": 1000.0, "invested": 11000.0, "ppl": 0.0},
    "accounts": [],
    "metrics": {},
    "positions": [
        # NVDA held in both accounts: Views merges the rows, Home doesn't — totals must match.
        {"account_kind": "invest", "ticker": "NVDA", "yfinance_ticker": "NVDA", "name": "NVIDIA", "value": 3000.0, "total_cost": 2500.0, "ppl": 500.0, "quantity": 10},
        {"account_kind": "stocks_isa", "ticker": "NVDA", "yfinance_ticker": "NVDA", "name": "NVIDIA", "value": 2000.0, "total_cost": 1500.0, "ppl": 500.0, "quantity": 7},
        {"account_kind": "stocks_isa", "ticker": "VUSA", "yfinance_ticker": "VUSA.L", "name": "S&P 500", "value": 5000.0, "total_cost": 4000.0, "ppl": 1000.0, "quantity": 50, "instrument_currency": "GBX"},
        {"account_kind": "invest", "ticker": "MSFT", "yfinance_ticker": "MSFT", "name": "Microsoft", "value": 1000.0, "total_cost": 900.0, "ppl": 100.0, "quantity": 2},
    ],
}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    from app.services import leveraged_market, portfolio_service

    day_move._cache.clear()
    monkeypatch.setattr(leveraged_market, "get_price_history", lambda sym, period="5d", interval="1d": CANDLES.get(sym, []))
    monkeypatch.setattr(portfolio_service, "get_portfolio_snapshot", lambda db, **kw: SNAPSHOT)
    yield
    day_move._cache.clear()


def expected_total() -> float:
    def mv(value, pct):
        return value - value / (1 + pct)

    return mv(5000, -0.02) + mv(5000, 0.01) + mv(1000, 0.01)


def test_home_and_views_agree_on_todays_move(monkeypatch):
    home = day_move.today_move(None)
    assert home["day_change"] == pytest.approx(expected_total(), abs=0.02)
    assert home["session"] == "2026-10-12" and home["holdings_counted"] == 4

    views_data._cache.clear()
    data = views_data._portfolio(None, {})
    rows = {r["ticker"]: r for r in data["positions"]}
    assert rows["NVDA"]["value"] == 5000.0  # merged across accounts
    views_total = sum(r["day_change"] for r in data["positions"])
    assert views_total == pytest.approx(home["day_change"], abs=0.02)
    assert rows["NVDA"]["day_change_pct"] == pytest.approx(-0.02)


def test_summary_pct_and_session_label():
    rows = [{"value": 1010.0, "day_change": 10.0, "day_session": "2026-10-12"}, {"value": 500.0, "day_change": None}]
    s = day_move.summarize(rows, today=date(2026, 10, 13))
    assert s["day_change"] == 10.0 and s["day_change_pct"] == pytest.approx(0.01)
    assert s["is_today"] is False and s["holdings_missing"] == 1
    assert day_move.summarize([], today=date(2026, 10, 13))["day_change"] is None
