"""fetch_history must negative-cache symbols that return no usable data.

A delisted / illiquid / unpriceable ticker (e.g. T212 codes ALCC1, NUCGL) makes
two yfinance attempts with a cookie-clear + sleep between them — roughly ~12.5s of
synchronous cost. Without a negative cache that is re-paid on EVERY call, because
only successful frames are cached. A short-TTL negative cache lets repeated
lookups of a bad symbol fail fast instead.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import app.services.market_data as md


class _EmptyTicker:
    constructed = 0

    def __init__(self, symbol):
        _EmptyTicker.constructed += 1

    def history(self, **kwargs):
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    md._HISTORY_CACHE.clear()
    neg = getattr(md, "_NEG_CACHE", None)
    if neg is not None:
        neg.clear()
    md._YF_THROTTLED_UNTIL = 0.0
    _EmptyTicker.constructed = 0
    monkeypatch.setattr(md.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(md, "_clear_yfinance_cookie_cache", lambda: None)
    monkeypatch.setattr(md.yf, "Ticker", _EmptyTicker)
    yield


def test_empty_symbol_is_negative_cached_and_not_refetched():
    with pytest.raises(md.MarketDataError):
        md.fetch_history("BADXYZ", lookback_days=60)
    after_first = _EmptyTicker.constructed
    assert after_first >= 1  # it really did hit the provider the first time

    # Second call within the negative-cache TTL must fail fast — no new provider hit.
    with pytest.raises(md.MarketDataError):
        md.fetch_history("BADXYZ", lookback_days=60)
    assert _EmptyTicker.constructed == after_first


def test_negative_cache_is_keyed_by_ticker_across_lookbacks():
    with pytest.raises(md.MarketDataError):
        md.fetch_history("BADXYZ", lookback_days=60)
    after_first = _EmptyTicker.constructed

    # Same bad ticker, different lookback + auto_adjust → still short-circuits.
    with pytest.raises(md.MarketDataError):
        md.fetch_history("BADXYZ", lookback_days=420, auto_adjust=False)
    assert _EmptyTicker.constructed == after_first
