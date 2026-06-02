"""Endpoint tests for the Instrument Spotlight router. Hermetic: an in-memory DB
via a get_db override, with the metadata map, live price, technicals, and portfolio
snapshot stubbed so we exercise the *aggregation* (held position, watchlist context,
open alerts, theses, price-vs-target verdict) without any network."""

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, get_db
from app.main import app
from app.models import entities  # noqa: F401
from app.models.entities import Alert, Thesis
from app.services import leveraged_market, portfolio_service, watchlist_service


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    yield Session
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    def _override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)


_META = {"NVDA_US_EQ": {"name": "NVIDIA Corp", "ticker": "NVDA_US_EQ", "currency": "USD"}}

_NVDA_POSITION = {
    "account_kind": "invest",
    "ticker": "NVDA",
    "instrument_code": "NVDA_US_EQ",
    "name": "NVIDIA Corp",
    "yfinance_ticker": "NVDA",
    "instrument_currency": "USD",
    "quantity": 10.0,
    "average_price": 100.0,
    "current_price": 180.0,
    "total_cost": 1000.0,
    "value": 1800.0,
    "ppl": 800.0,
    "weight": 0.25,
    "momentum_63d": 0.12,
    "rsi_14": 62.0,
    "trend_score": 1.0,
    "volatility_30d": 0.3,
    "risk_flag": "ok",
}


def _seed_nvda(session_factory):
    db = session_factory()
    watchlist_service.add_item(
        db, "NVDA", note="AI leader", conviction="high", target_price=200.0, target_direction="above"
    )
    db.add(Thesis(symbol="NVDA", title="AI tailwind", thesis="datacenter demand",
                  invalidation="below 150", confidence=0.72, status="active"))
    db.add(Alert(category="big_move", severity="warning", ticker="NVDA", title="NVDA +8% today",
                 detail="big intraday move", status="new", dedupe_key="nvda-move", source="watch"))
    db.commit()
    db.close()


def test_search_resolves_symbol(client, monkeypatch):
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: _META)

    resp = client.get("/api/instruments/search?q=nvd")
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert any(r["ticker"] == "NVDA" and r["instrument_code"] == "NVDA_US_EQ" for r in results)


def test_detail_aggregates_held_instrument(client, session_factory, monkeypatch):
    _seed_nvda(session_factory)
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: _META)
    monkeypatch.setattr(
        portfolio_service, "get_portfolio_snapshot",
        lambda db, ak="all", dc=None, **kw: {"positions": [_NVDA_POSITION], "account": {}, "metrics": {}},
    )
    monkeypatch.setattr(
        leveraged_market, "get_price",
        lambda t, c=None: {"ticker": "NVDA", "yfinance_ticker": "NVDA", "price": 180.0,
                           "currency": "USD", "is_minor_unit": False, "change_pct": 0.015},
    )

    resp = client.get("/api/instruments/NVDA/detail")
    assert resp.status_code == 200
    body = resp.json()

    assert body["held"] is True
    assert body["position"]["value"] == 1800.0
    assert body["position"]["ppl_pct"] == pytest.approx(0.8)
    assert body["position"]["accounts"] == ["invest"]

    assert body["watchlist"]["conviction"] == "high"
    assert body["target_price"] == 200.0
    assert body["target_distance_pct"] == pytest.approx((200.0 - 180.0) / 180.0)

    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["severity"] == "warning"
    assert len(body["theses"]) == 1
    assert body["theses"][0]["confidence"] == pytest.approx(0.72)
    assert body["signals"]["rsi_14"] == 62.0
    assert body["change_pct"] == pytest.approx(0.015)


def test_detail_unheld_falls_back_gracefully(client, monkeypatch):
    # No metadata + no snapshot match → identity falls back to the typed term, and
    # signals come from technicals rather than a (missing) held row.
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: {})
    monkeypatch.setattr(
        portfolio_service, "get_portfolio_snapshot",
        lambda db, ak="all", dc=None, **kw: {"positions": [], "account": {}, "metrics": {}},
    )
    monkeypatch.setattr(
        leveraged_market, "get_price",
        lambda t, c=None: {"ticker": "AAPL", "yfinance_ticker": "AAPL", "price": 210.0,
                           "currency": "USD", "is_minor_unit": False, "change_pct": -0.01},
    )
    monkeypatch.setattr(
        leveraged_market, "get_technicals",
        lambda t, period="6mo": {"rsi_14": 55.0, "trend_direction": "uptrend"},
    )

    resp = client.get("/api/instruments/AAPL/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["held"] is False
    assert body["position"] is None
    assert body["price"] == 210.0
    assert body["signals"]["rsi_14"] == 55.0
    assert body["signals"]["trend_direction"] == "uptrend"
    assert body["watchlist"] is None
    assert body["alerts"] == []
    assert body["theses"] == []


@pytest.mark.parametrize(
    "code,short,expected",
    [
        ("SNDK1_US_EQ", "SNDK", "SNDK"),   # renamed/suffixed US equity → real Nasdaq symbol
        ("YNDX_US_EQ", "NBIS", "NBIS"),    # SPAC/rename → current symbol
        ("AAPL_US_EQ", "AAPL", "AAPL"),    # normal US equity, unchanged
        ("AAPL_US_EQ", "", "AAPL"),        # no shortName → legacy symbol
        ("AAPL_US_EQ", "Apple Inc.", "AAPL"),  # multi-word shortName is not a ticker → legacy
        ("GOOGL_EQ", "GOOG", "GOOGL"),     # LSE ETP (not _US_EQ): keep structural resolution, NOT the underlying
    ],
)
def test_market_yfinance_ticker(code, short, expected):
    assert portfolio_service.market_yfinance_ticker(code, short, "USD") == expected
