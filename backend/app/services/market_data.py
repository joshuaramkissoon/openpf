from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

from app.services.leveraged_market import _clear_yfinance_cookie_cache


class MarketDataError(RuntimeError):
    pass


logger = logging.getLogger(__name__)

OFFLINE_MODE = False
CACHE_TTL_SECONDS = 15 * 60
MAX_CACHE_ITEMS = 512
_HISTORY_CACHE: dict[tuple[str, int], tuple[float, pd.DataFrame]] = {}
_YF_THROTTLED_UNTIL = 0.0

# Avoid flooding logs when provider throttles.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)


def normalize_symbol_for_yf(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        return ""
    if "_" in value:
        value = value.split("_")[0]
    value = value.replace(" ", "")
    value = "".join(ch for ch in value if ch.isalnum() or ch in ".-")
    return value


def _get_cached(key: tuple[str, int], *, allow_stale: bool = False) -> pd.DataFrame | None:
    cached = _HISTORY_CACHE.get(key)
    if not cached:
        return None
    ts, frame = cached
    age = time.time() - ts
    if age <= CACHE_TTL_SECONDS or allow_stale:
        return frame.copy()
    return None


def _set_cache(key: tuple[str, int], frame: pd.DataFrame) -> pd.DataFrame:
    if len(_HISTORY_CACHE) >= MAX_CACHE_ITEMS:
        # Evict oldest entry.
        oldest_key = min(_HISTORY_CACHE.items(), key=lambda item: item[1][0])[0]
        _HISTORY_CACHE.pop(oldest_key, None)
    _HISTORY_CACHE[key] = (time.time(), frame.copy())
    return frame


def _synthetic_history(symbol: str, lookback_days: int) -> pd.DataFrame:
    seed = abs(hash(symbol)) % (2**32)
    rng = np.random.default_rng(seed)
    periods = max(lookback_days, 180)
    end = date.today()
    dates = [end - timedelta(days=i) for i in range(periods)][::-1]

    drift = rng.uniform(-0.0002, 0.0008)
    vol = rng.uniform(0.01, 0.025)
    returns = rng.normal(drift, vol, size=periods)
    price0 = rng.uniform(40.0, 250.0)
    close = price0 * np.exp(np.cumsum(returns))
    high = close * (1 + rng.uniform(0.0, 0.01, size=periods))
    low = close * (1 - rng.uniform(0.0, 0.01, size=periods))
    open_ = close * (1 + rng.normal(0, 0.004, size=periods))
    volume = rng.integers(500_000, 8_000_000, size=periods)

    frame = pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    return frame.reset_index(drop=True)


def fetch_history(symbol: str, lookback_days: int = 420) -> pd.DataFrame:
    global _YF_THROTTLED_UNTIL

    ticker = normalize_symbol_for_yf(symbol)
    if not ticker:
        raise MarketDataError(f"Invalid symbol for market data: {symbol!r}")
    cache_key = (ticker, int(lookback_days))

    cached = _get_cached(cache_key)
    if cached is not None:
        return cached

    if OFFLINE_MODE:
        return _set_cache(cache_key, _synthetic_history(ticker, lookback_days))

    now = time.time()
    if now < _YF_THROTTLED_UNTIL:
        stale = _get_cached(cache_key, allow_stale=True)
        if stale is not None:
            return stale
        return _set_cache(cache_key, _synthetic_history(ticker, lookback_days))

    end = date.today() + timedelta(days=1)
    start = date.today() - timedelta(days=lookback_days)

    # Use Ticker.history() instead of yf.download() to avoid MultiIndex
    # issues and get more reliable single-ticker results.  Retry once
    # after clearing the cookie cache on failure or empty data.
    data = pd.DataFrame()
    for attempt in range(2):
        try:
            t = yf.Ticker(ticker)
            data = t.history(
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                timeout=6,
                raise_errors=True,
            )
        except Exception:
            if attempt == 0:
                logger.warning("yfinance fetch_history attempt 1 failed for %s, clearing cookies", ticker)
                _clear_yfinance_cookie_cache()
                time.sleep(0.5)
                continue
            _YF_THROTTLED_UNTIL = time.time() + 300
            stale = _get_cached(cache_key, allow_stale=True)
            if stale is not None:
                return stale
            return _set_cache(cache_key, _synthetic_history(ticker, lookback_days))

        if not data.empty:
            break

        if attempt == 0:
            logger.warning("yfinance returned empty data for %s, clearing cookies and retrying", ticker)
            _clear_yfinance_cookie_cache()
            time.sleep(0.5)

    if data.empty:
        _YF_THROTTLED_UNTIL = time.time() + 300
        stale = _get_cached(cache_key, allow_stale=True)
        if stale is not None:
            return stale
        return _set_cache(cache_key, _synthetic_history(ticker, lookback_days))

    frame = data.reset_index()
    # Ticker.history() returns flat columns, but guard against MultiIndex.
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(col[0]).lower() for col in frame.columns]
    else:
        frame.columns = [str(col).lower() for col in frame.columns]
    if "date" not in frame.columns:
        frame = frame.rename(columns={frame.columns[0]: "date"})

    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in frame.columns]
    out = frame[keep].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date
    out = out.dropna(subset=["close"]).reset_index(drop=True)
    if out.empty:
        _YF_THROTTLED_UNTIL = time.time() + 300
        stale = _get_cached(cache_key, allow_stale=True)
        if stale is not None:
            return stale
        return _set_cache(cache_key, _synthetic_history(ticker, lookback_days))
    return _set_cache(cache_key, out)
