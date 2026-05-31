"""Tests for the market-driven leveraged universe.

`build_underlying_map` is a pure transform of T212 instrument metadata — tested
with synthetic instruments. `build_universe`'s ranking/gating is tested with
monkeypatched technicals so no network is involved.
"""

from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services import leveraged_universe as lu
from app.services.regime_service import RegimeState


# Synthetic T212 metadata covering long+inverse on the same names.
INSTRUMENTS = [
    {"ticker": "3SNDl_EQ", "name": "Leverage Shares 3x Long SanDisk SNDK (Acc)", "currencyCode": "GBX", "type": "ETF"},
    {"ticker": "SSNDl_EQ", "name": "Leverage Shares -3x Short SanDisk SNDK (Acc)", "currencyCode": "GBX", "type": "ETF"},
    {"ticker": "3NVDl_EQ", "name": "Leverage Shares 3x Long NVIDIA NVDA (Acc)", "currencyCode": "GBX", "type": "ETF"},
    {"ticker": "3SNVl_EQ", "name": "Leverage Shares -3x Short NVIDIA NVDA (Acc)", "currencyCode": "GBX", "type": "ETF"},
    {"ticker": "AAPL_US_EQ", "name": "Apple", "currencyCode": "USD", "type": "STOCK"},  # not leveraged → skipped
    {"ticker": "2MUl_EQ", "name": "Leverage Shares 2x Micron Technology MU", "currencyCode": "GBX", "type": "ETF"},
]


@pytest.fixture(autouse=True)
def _clear_cache():
    lu._map_cache = None
    lu._universe_cache = None
    yield
    lu._map_cache = None
    lu._universe_cache = None


def test_underlying_map_pairs_long_and_inverse():
    m = lu.build_underlying_map(INSTRUMENTS)
    assert "SNDK" in m and "NVDA" in m
    assert m["SNDK"]["long"]["ticker"] == "3SNDl_EQ"
    assert m["SNDK"]["inverse"]["ticker"] == "SSNDl_EQ"
    assert m["NVDA"]["long"]["ticker"] == "3NVDl_EQ"
    assert m["NVDA"]["inverse"]["ticker"] == "3SNVl_EQ"
    # Non-leveraged Apple is not in the map.
    assert "AAPL" not in m
    # Micron has only a long product here.
    assert m["MU"]["long"]["ticker"] == "2MUl_EQ"
    assert m["MU"]["inverse"] is None


def _regime(name, score):
    lb = max(0.0, min(1.0, 0.5 + score / 2))
    return RegimeState(name, name.title(), score, lb, 1 - lb, 15.0, "calm", 1.0, "t", {}, "t", False)


def _patch(monkeypatch, trends: dict[str, str]):
    monkeypatch.setattr(lu, "get_underlying_map", lambda db, force=False: _MAP)

    def fake_tech(proxy, period="6mo"):
        trend = trends.get(proxy, "mixed")
        price = 100.0
        return {
            "trend_direction": trend,
            "price": price,
            "sma_20": 95.0 if trend == "uptrend" else 105.0,
            "sma_50": 90.0 if trend == "uptrend" else 110.0,
            "rsi_14": 60.0 if trend == "uptrend" else 40.0,
        }

    monkeypatch.setattr(lu, "get_technicals", fake_tech)


_MAP = {
    "SNDK": {"underlying": "SNDK", "underlying_name": "SanDisk", "proxy": "SNDK",
             "long": {"ticker": "3SNDl_EQ", "name": "3x Long SanDisk", "factor": 3, "currency": "GBX"},
             "inverse": {"ticker": "SSNDl_EQ", "name": "-3x Short SanDisk", "factor": 3, "currency": "GBX"}},
    "NVDA": {"underlying": "NVDA", "underlying_name": "NVIDIA", "proxy": "NVDA",
             "long": {"ticker": "3NVDl_EQ", "name": "3x Long NVDA", "factor": 3, "currency": "GBX"},
             "inverse": {"ticker": "3SNVl_EQ", "name": "-3x Short NVDA", "factor": 3, "currency": "GBX"}},
}


def test_universe_risk_on_picks_long_for_uptrend(monkeypatch):
    _patch(monkeypatch, {"SNDK": "uptrend", "NVDA": "uptrend"})
    out = lu.build_universe(None, _regime("risk_on", 0.8), candidates=["SNDK", "NVDA"])
    assert len(out["ranked"]) == 2
    for r in out["ranked"]:
        assert r["direction"] == "long"
        assert r["regime_aligned"] is True
        assert r["etp_ticker"].startswith("3")


def test_universe_risk_on_drops_downtrend_inverse(monkeypatch):
    # Strong risk-on; a downtrending name would want an inverse ETP — must be dropped.
    _patch(monkeypatch, {"SNDK": "downtrend", "NVDA": "uptrend"})
    out = lu.build_universe(None, _regime("risk_on", 0.8), candidates=["SNDK", "NVDA"])
    tickers = [r["underlying"] for r in out["ranked"]]
    assert "SNDK" not in tickers  # inverse pick dropped in strong risk-on
    assert "NVDA" in tickers


def test_universe_risk_off_picks_inverse_for_downtrend(monkeypatch):
    _patch(monkeypatch, {"SNDK": "downtrend", "NVDA": "downtrend"})
    out = lu.build_universe(None, _regime("risk_off", -0.8), candidates=["SNDK", "NVDA"])
    assert len(out["ranked"]) == 2
    for r in out["ranked"]:
        assert r["direction"] == "inverse"
        assert r["regime_aligned"] is True


def test_universe_flags_degraded_when_no_instruments(monkeypatch):
    # T212 outage → empty underlying map → result must be flagged degraded,
    # not silently presented as an empty (quiet) market.
    monkeypatch.setattr(lu, "get_underlying_map", lambda db, force=False: {})
    out = lu.build_universe(None, _regime("risk_on", 0.8))
    assert out["degraded"] is True
    assert out["available_underlyings"] == 0
    assert out["error_reason"]
    assert out["ranked"] == []
