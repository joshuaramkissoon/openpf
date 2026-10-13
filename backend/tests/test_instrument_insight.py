"""Tests for the stock sheet's aggregated ``/instruments/{ticker}/insight``.

Hermetic: an in-memory DB, the portfolio snapshot and instrument metadata
stubbed, and every network fetcher in ``instrument_insight`` replaced, so we
exercise the aggregation — currency consistency, the cost line, trade markers,
risk share, plays, unknown tickers and missing sources — without the network.
"""

from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, get_db
from app.main import app
from app.models import entities  # noqa: F401
from app.models.entities import Play, TradeIntent
from app.services import instrument_insight as ii
from app.services import portfolio_service, proposal_service

USD_GBP = 0.75


@pytest.fixture()
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    yield Session
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    def _override():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


def _frame(n=300, start=100.0, seed=1, currency="USD"):
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + rng.normal(0.0005, 0.02, n))
    dates = pd.bdate_range(end="2026-10-12", periods=n, tz="UTC")
    df = pd.DataFrame({"date": dates, "open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes, "volume": 1e6})
    df.attrs["currency"] = currency
    return df


_FRAMES = {"NVDA": _frame(seed=1), "TSM": _frame(seed=2), "MSFT": _frame(seed=3), "SPY": _frame(seed=4, start=500)}

_NVDA_ROW = {
    "account_kind": "stocks_isa", "ticker": "NVDA", "display_ticker": "NVDA", "instrument_code": "NVDA_US_EQ",
    "name": "NVIDIA", "yfinance_ticker": "NVDA", "instrument_currency": "USD", "quantity": 10.0,
    # Snapshot prices are converted to GBP: avg $150 → £112.50.
    "average_price": 150.0 * USD_GBP, "current_price": 180.0 * USD_GBP, "total_cost": 1125.0,
    "value": 1350.0, "ppl": 225.0, "weight": 0.135,
}
_MSFT_ROW = {**_NVDA_ROW, "ticker": "MSFT", "display_ticker": "MSFT", "instrument_code": "MSFT_US_EQ", "name": "Microsoft",
             "yfinance_ticker": "MSFT", "value": 2000.0, "weight": 0.2}
_TSM_ROW = {**_NVDA_ROW, "ticker": "TSM", "display_ticker": "TSM", "instrument_code": "TSM_US_EQ", "name": "TSMC",
            "yfinance_ticker": "TSM", "value": 1500.0, "weight": 0.15}


def _snapshot(rows):
    return {
        "positions": rows,
        "metrics": {"total_value": 10000.0, "free_cash": 1000.0},
        "accounts": [{"account_kind": "stocks_isa", "free_cash": 1000.0}],
    }


@pytest.fixture()
def stubs(monkeypatch):
    ii.clear_cache()
    state = {"rows": [_NVDA_ROW, _MSFT_ROW, _TSM_ROW], "frames": dict(_FRAMES), "fail": set()}

    def ident(term, db):
        sym = term.upper()
        if sym in {"NVDA", "MSFT", "TSM", "CRWD", "VUSA"}:
            return {"instrument_code": f"{sym}_US_EQ", "ticker": sym, "display_ticker": sym, "name": sym.title(), "currency": "USD"}
        return None

    def hist(t, period="5y"):
        if "hist" in state["fail"] or t not in state["frames"]:
            raise RuntimeError("no data")
        return state["frames"][t]

    def guard(name, value):
        def _fn(*a, **k):
            if name in state["fail"]:
                raise RuntimeError(f"{name} down")
            return value
        return _fn

    monkeypatch.setattr(portfolio_service, "resolve_primary_instrument", ident)
    monkeypatch.setattr(portfolio_service, "get_portfolio_snapshot", lambda db, *a, **k: _snapshot(state["rows"]))
    monkeypatch.setattr(ii, "fetch_daily_history", hist)
    monkeypatch.setattr(ii, "fetch_info", guard("info", {"trailingPE": 40.0, "forwardPE": 30.0, "grossMargins": 0.7, "industry": "Semiconductors", "financialCurrency": "USD"}))
    monkeypatch.setattr(ii, "fetch_earnings_frame", guard("earnings", None))
    monkeypatch.setattr(ii, "fetch_news", guard("news", [{"headline": "NVDA beats", "source": "Wire", "url": "https://x", "ts": 1}]))
    monkeypatch.setattr(ii, "order_clients", lambda store: [])
    monkeypatch.setattr(ii, "fetch_orders", guard("orders", [{"date": "2026-06-01", "side": "buy", "quantity": 10.0, "price": 150.0, "account_kind": "stocks_isa"}]))
    monkeypatch.setattr(ii, "explain_move", lambda *a, **k: None)
    monkeypatch.setattr(ii, "get_fx_rate", lambda b, q: 1.0 if b == q else (USD_GBP if (b, q) == ("USD", "GBP") else 1 / USD_GBP))
    yield state
    ii.clear_cache()


def _get(client, t):
    return client.get(f"/api/instruments/{t}/insight?wait=8")


def test_clean_daily_drops_glitches():
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"], utc=True),
        "open": [100, 101, 101, 0, 102, 103], "high": [101, 102, 102, 10200, 103, 104],
        "low": [99, 100, 100, 100, 101, 102], "close": [100, 100.5, 101, 10150, 102, 0],
    })
    out = ii.clean_daily(df)
    # duplicate session collapsed, 100x bad print and zero close dropped
    assert out["day"].tolist() == ["2026-01-05", "2026-01-06", "2026-01-08"]
    assert out["close"].tolist() == [100, 101, 102]


def test_held_insight_is_currency_consistent(client, session_factory, stubs):
    db = session_factory()
    db.add(Play(title="AI compute", status="active", budget_pct_of_book=0.2,
                legs=[{"ticker": "NVDA", "role": "core", "target_weight": 0.5, "rationale": "Accelerators"}],
                kill_criteria=[{"text": "NVDA below 100", "rule": {"type": "price_below", "ticker": "NVDA", "value": 100}}]))
    db.add(Play(title="Semis", status="active", legs=[{"ticker": "AMD", "target_weight": 1.0}], meta={"related": ["NVDA"]}))
    db.commit()
    db.close()

    body = _get(client, "NVDA").json()
    last_close = float(_FRAMES["NVDA"]["close"].iloc[-1])

    # Header price, chart axis and cost line all in USD.
    assert body["quote"]["currency"] == "USD"
    assert body["chart"]["currency"] == "USD"
    assert body["quote"]["price"] == pytest.approx(last_close)
    assert body["chart"]["points"][-1][4] == pytest.approx(last_close)
    assert body["quote"]["price_gbp"] == pytest.approx(last_close * USD_GBP)
    assert body["chart"]["avg_cost"] == pytest.approx(150.0)
    assert body["chart"]["trades"][0]["price"] == pytest.approx(150.0)

    pos = body["position"]
    assert pos["value_gbp"] == 1350.0 and pos["weight"] == pytest.approx(0.135)

    risk = body["risk"]
    assert 0 < risk["risk_share"] < 1
    assert risk["value_share"] == pytest.approx(0.135)
    assert {m["ticker"] for m in risk["moves_with"]} <= {"MSFT", "TSM"}

    owner, related = body["plays"]
    assert owner["relation"] == "owner" and owner["role"] == "core"
    assert owner["target_weight_of_book"] == pytest.approx(0.1)
    assert owner["rules"][0]["status"] == "fine"
    assert related["relation"] == "related" and related["title"] == "Semis"

    assert body["fundamentals"]["pe_forward"] == 30.0
    assert body["news"][0]["headline"] == "NVDA beats"
    assert body["book"]["position_cap"] is not None
    assert body["pending"] == []


def test_sheet_counts_only_what_the_play_owns_for_its_concentration_rule(client, session_factory, stubs):
    """Regression: the sheet evaluated kill rules off-thread with no DB session, so
    ownership fell back to *every* holding in the play's account. A play owning
    13.5% read as 48.5% and its 'grows past 30% of the book' rule as Triggered."""
    db = session_factory()
    db.add(Play(title="AI compute", status="active", account_kind="stocks_isa",
                legs=[{"ticker": "NVDA", "role": "core", "target_weight": 1.0, "rationale": "Accelerators"}],
                kill_criteria=[{"text": "This bet grows past 30% of the book",
                                "rule": {"type": "weight_above", "ticker": None, "value": 0.3}}]))
    db.commit()
    db.close()
    owner = _get(client, "NVDA").json()["plays"][0]
    rule = owner["rules"][0]
    assert rule["status"] == "fine", rule
    assert "13.5%" in rule["detail"]
    assert owner["verdict"] == "on_track"


def test_pence_series_is_shown_in_pounds(client, stubs):
    stubs["frames"]["VUSA"] = _frame(seed=5, start=9000.0, currency="GBp")
    body = _get(client, "VUSA").json()
    assert body["chart"]["currency"] == "GBP"
    assert body["chart"]["unit_multiplier"] == 0.01
    assert body["quote"]["price"] == pytest.approx(float(stubs["frames"]["VUSA"]["close"].iloc[-1]) * 0.01)


def test_not_held_has_no_position_but_keeps_context(client, stubs):
    stubs["frames"]["CRWD"] = _frame(seed=6)
    body = _get(client, "CRWD").json()
    assert body["held"] is False and body["position"] is None
    assert body["risk"]["risk_share"] is None
    assert body["risk"]["moves_with"]  # still says what it would move with
    assert body["book"]["total_gbp"] == 10000.0
    assert body["chart"]["avg_cost"] is None and body["chart"]["trades"] == []


def test_unknown_ticker_is_404(client, stubs):
    resp = _get(client, "ZZZQX")
    assert resp.status_code == 404


def test_missing_sources_hide_sections(client, stubs):
    stubs["fail"].update({"hist", "info", "earnings", "news", "orders"})
    resp = _get(client, "NVDA")
    assert resp.status_code == 200  # known instrument: degrade, don't fail
    body = resp.json()
    assert body["chart"] is None and body["quote"] is None
    assert body["fundamentals"] is None and body["events"] is None
    assert body["news"] == [] and body["risk"] is None
    assert body["position"]["value_gbp"] == 1350.0  # DB-local sections still there
    assert body["pending"] == []


def test_no_broker_snapshot_still_works(client, stubs, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no broker")

    monkeypatch.setattr(portfolio_service, "get_portfolio_snapshot", boom)
    body = _get(client, "NVDA").json()
    assert body["held"] is False and body["book"] is None
    assert body["quote"]["currency"] == "USD"


def test_propose_from_sheet_only_proposes(client, session_factory, stubs, monkeypatch):
    calls = {}

    def fake(db, **kw):
        calls.update(kw)
        intent = TradeIntent(status="proposed", symbol="NVDA", instrument_code=kw["instrument_code"], side=kw["side"], quantity=1.0, order_type="market",
                             estimated_notional=kw["notional_gbp"], rationale=kw["rationale"], meta={})
        db.add(intent)
        db.commit()
        return {"ok": True, "intent": {"id": intent.id}}

    monkeypatch.setattr(proposal_service, "propose_trade", fake)
    resp = client.post("/api/instruments/NVDA/propose", json={"side": "buy", "account_kind": "stocks_isa", "notional_gbp": 250})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert calls["source"] == "instrument_sheet"
    assert calls["instrument_code"] == "NVDA_US_EQ"
    db = session_factory()
    assert [i.status for i in db.query(TradeIntent).all()] == ["proposed"]
    db.close()
