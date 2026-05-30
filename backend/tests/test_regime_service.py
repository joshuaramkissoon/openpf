"""Deterministic tests for the market-regime classifier.

Market data is monkeypatched so the regime logic is tested in isolation — no
network, no yfinance — exactly the inputs → classification mapping.
"""

from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import regime_service as rs


def _tech(trend, price, sma50, sma200):
    return {
        "price": price,
        "sma_50": sma50,
        "sma_200": sma200,
        "trend_direction": trend,
        "rsi_14": 55.0,
        "macd": 0.0,
        "macd_signal": 0.0,
    }


@pytest.fixture(autouse=True)
def _clear_cache():
    # Each test computes fresh — the module caches for 10 min otherwise.
    rs._cache = None
    yield
    rs._cache = None


def _patch(monkeypatch, *, spy, qqq, vix):
    def fake_tech(symbol, period="1y"):
        return {"SPY": spy, "QQQ": qqq}[symbol]

    def fake_price(symbol, currency_code=None):
        if symbol == "^VIX":
            if vix is None:
                raise rs.LeveragedMarketError("no VIX")
            return {"price": vix}
        raise rs.LeveragedMarketError(f"unexpected {symbol}")

    monkeypatch.setattr(rs, "get_technicals", fake_tech)
    monkeypatch.setattr(rs, "get_price", fake_price)


def test_risk_on(monkeypatch):
    up = _tech("uptrend", 760, 700, 670)
    _patch(monkeypatch, spy=up, qqq=up, vix=14.0)
    r = rs.compute_regime(force=True)
    assert r.regime == "risk_on"
    assert r.score > 0.3
    assert r.long_bias > r.inverse_bias
    assert r.favours("long") and not r.favours("inverse")
    assert r.vix_state == "calm"


def test_risk_off(monkeypatch):
    down = _tech("downtrend", 600, 650, 700)
    _patch(monkeypatch, spy=down, qqq=down, vix=24.0)
    r = rs.compute_regime(force=True)
    assert r.regime == "risk_off"
    assert r.score < -0.3
    assert r.inverse_bias > r.long_bias
    assert r.favours("inverse") and not r.favours("long")


def test_neutral_mixed(monkeypatch):
    up = _tech("uptrend", 760, 700, 670)
    down = _tech("downtrend", 600, 650, 700)
    _patch(monkeypatch, spy=up, qqq=down, vix=18.0)
    r = rs.compute_regime(force=True)
    assert r.regime == "neutral"
    # Neutral favours nothing strongly.
    assert r.favours("long") and r.favours("inverse")


def test_vix_stress_overrides_uptrend(monkeypatch):
    # Strong uptrend but a stressed VIX must NOT read as risk-on — and must NOT
    # flip to risk-off (which would favour inverse ETPs into an uptrend). It
    # reads NEUTRAL: defensive, no counter-trend tilt.
    up = _tech("uptrend", 760, 700, 670)
    _patch(monkeypatch, spy=up, qqq=up, vix=34.0)
    r = rs.compute_regime(force=True)
    assert r.regime == "neutral"
    assert r.vix_state == "stressed"


def test_vix_stress_with_downtrend_is_risk_off(monkeypatch):
    down = _tech("downtrend", 600, 650, 700)
    _patch(monkeypatch, spy=down, qqq=down, vix=34.0)
    r = rs.compute_regime(force=True)
    assert r.regime == "risk_off"


def test_degraded_data_is_neutral_not_fabricated(monkeypatch):
    def boom_tech(symbol, period="1y"):
        raise rs.LeveragedMarketError("provider down")

    def boom_price(symbol, currency_code=None):
        raise rs.LeveragedMarketError("provider down")

    monkeypatch.setattr(rs, "get_technicals", boom_tech)
    monkeypatch.setattr(rs, "get_price", boom_price)
    r = rs.compute_regime(force=True)
    assert r.regime == "neutral"
    assert r.stale is True
    assert r.long_bias == 0.5 and r.inverse_bias == 0.5
    assert "degraded" in r.rationale.lower()
