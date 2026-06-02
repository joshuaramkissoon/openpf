"""`/charts/candles` should cache built responses so repeated chart mounts
don't re-hit yfinance every time.

The candle download (`_download_history_frame`) goes straight to live yfinance
on every call. Opening the same chat repeatedly (or two clients viewing the
same ticker) should reuse a recent result within a short TTL, keyed by the
exact request params.
"""
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd
from fastapi.testclient import TestClient

from app.api import charts
from app.main import app

client = TestClient(app)


def _fake_frame() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=5, freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [1.0, 2.0, 3.0, 4.0, 5.0],
            "high": [1.5, 2.5, 3.5, 4.5, 5.5],
            "low": [0.5, 1.5, 2.5, 3.5, 4.5],
            "close": [1.2, 2.2, 3.2, 4.2, 5.2],
            "volume": [100, 200, 300, 400, 500],
        }
    )


def _counting_loader():
    calls = {"n": 0}

    def _loader(ticker: str, period: str, interval: str):
        calls["n"] += 1
        return _fake_frame()

    return _loader, calls


def setup_function(_):
    charts._clear_candle_cache()


def test_candles_cached_within_ttl(monkeypatch):
    loader, calls = _counting_loader()
    monkeypatch.setattr(charts, "_download_history_frame", loader)

    p = {"ticker": "MU", "period": "6mo", "interval": "1d", "indicators": "sma20,sma50"}
    r1 = client.get("/api/charts/candles", params=p)
    r2 = client.get("/api/charts/candles", params=p)

    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["candles"] == r2.json()["candles"]
    # Second identical request must be served from cache → loader called once.
    assert calls["n"] == 1


def test_candles_cache_key_varies_by_params(monkeypatch):
    loader, calls = _counting_loader()
    monkeypatch.setattr(charts, "_download_history_frame", loader)

    client.get("/api/charts/candles", params={"ticker": "MU", "period": "6mo", "interval": "1d"})
    client.get("/api/charts/candles", params={"ticker": "NVDA", "period": "6mo", "interval": "1d"})
    client.get("/api/charts/candles", params={"ticker": "MU", "period": "1y", "interval": "1d"})

    # Three distinct (ticker, period, interval) keys → three fetches.
    assert calls["n"] == 3


def test_candles_indicator_order_is_normalised(monkeypatch):
    loader, calls = _counting_loader()
    monkeypatch.setattr(charts, "_download_history_frame", loader)

    base = {"ticker": "MU", "period": "6mo", "interval": "1d"}
    client.get("/api/charts/candles", params={**base, "indicators": "sma20,sma50"})
    client.get("/api/charts/candles", params={**base, "indicators": "sma50,sma20"})

    # Same indicator set, different order → one cache entry.
    assert calls["n"] == 1


def test_clear_candle_cache_forces_refetch(monkeypatch):
    loader, calls = _counting_loader()
    monkeypatch.setattr(charts, "_download_history_frame", loader)

    p = {"ticker": "MU", "period": "6mo", "interval": "1d"}
    client.get("/api/charts/candles", params=p)
    charts._clear_candle_cache()
    client.get("/api/charts/candles", params=p)

    assert calls["n"] == 2


def test_candles_refetched_after_ttl(monkeypatch):
    loader, calls = _counting_loader()
    monkeypatch.setattr(charts, "_download_history_frame", loader)
    clock = {"t": 1_000.0}
    monkeypatch.setattr(charts.time, "time", lambda: clock["t"])

    p = {"ticker": "MU", "period": "6mo", "interval": "1d"}
    client.get("/api/charts/candles", params=p)            # stored at t=1000
    clock["t"] += charts._CANDLE_CACHE_TTL_DEFAULT + 1     # age past the TTL
    client.get("/api/charts/candles", params=p)            # stale -> re-fetch

    assert calls["n"] == 2


def test_errors_are_not_cached(monkeypatch):
    calls = {"n": 0}

    def _boom(ticker: str, period: str, interval: str):
        calls["n"] += 1
        raise charts.LeveragedMarketError("provider down")

    monkeypatch.setattr(charts, "_download_history_frame", _boom)

    p = {"ticker": "MU", "period": "6mo", "interval": "1d"}
    r1 = client.get("/api/charts/candles", params=p)
    r2 = client.get("/api/charts/candles", params=p)

    assert r1.status_code == 400 and r2.status_code == 400
    # A failed fetch must not be cached — the next request retries.
    assert calls["n"] == 2
