from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class LeveragedMarketError(RuntimeError):
    pass


_CACHE_TTL_SECONDS = 300
_CACHE_MAX_ITEMS = 512
_cache_lock = Lock()
_price_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_history_cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
_technical_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _clear_yfinance_cookie_cache() -> None:
    """Clear the yfinance cookie/crumb cache to force re-authentication.

    This fixes the common failure mode where stale cookies cause all
    requests to silently return empty data.
    """
    try:
        from yfinance.data import YfData
        yfdata = YfData(session=None)
        with yfdata._cookie_lock:
            yfdata._cookie = None
            yfdata._crumb = None
        logger.debug("Cleared yfinance cookie/crumb in-memory cache")
    except Exception:  # noqa: BLE001
        pass

    # Also clear the on-disk persistent cookie cache.
    try:
        from yfinance.cache import get_cookie_cache
        cookie_cache = get_cookie_cache()
        cookie_cache.store("basic", None)
        cookie_cache.store("csrf", None)
        logger.debug("Cleared yfinance persistent cookie cache")
    except Exception:  # noqa: BLE001
        pass


T212_TO_YFINANCE: dict[str, str] = {
    "3USL": "3USL.L",
    "3ULS": "3ULS.L",
    "3LUS": "3LUS.L",
    "LQQ3": "LQQ3.L",
    "QQQ3": "QQQ3.L",
    "QQQS": "QQQS.L",
    "3PLT": "3PLT.L",
    "3TSM": "3TSM.L",
    "3STS": "3STS.L",
    "3NVD": "3NVD.L",
    "3SNV": "3SNV.L",
    "3MSF": "3MSF.L",
    "3SMS": "3SMS.L",
    "3SAM": "3SAM.L",
    "3SPL": "3SPL.L",
    "3AVG": "3AVG.L",
    "3ASM": "3ASM.L",
    "3CON": "3CON.L",
    "3BAB": "3BAB.L",
    "3GOL": "3GOL.L",
    "3LGO": "3LGO.L",
    "3BRL": "3BRL.L",
    "3BLR": "3BLR.L",
    "3GOS": "3GOS.L",
    "3BSR": "3BSR.L",
    "3BRS": "3BRS.L",
    "3NGL": "3NGL.L",
    "3LGS": "3LGS.L",
    "3NGS": "3NGS.L",
    "3SLV": "3SLV.L",
    "AI3": "AI3.L",
    "GPT3": "GPT3.L",
    "3EML": "3EML.L",
    "3BAL": "3BAL.L",
    "3DEL": "3DEL.L",
    "3EUL": "3EUL.L",
    "3UKL": "3UKL.L",
    "MG3S": "MG3S.L",
    "3M7S": "3M7S.L",
    "3SSM": "3SSM.L",
    "SC3S": "SC3S.L",
    "UL3S": "UL3S.L",
    "3TYS": "3TYS.L",
}


# ──────────────────────────────────────────────────────────────────────────
# T212 instrument code → yfinance ticker resolution
#
# Trading 212 instrument codes encode the listing exchange, NOT just the
# symbol. Two forms exist:
#   • US / country-segment form:  ``AAPL_US_EQ``  →  yfinance ``AAPL``
#                                 ``FSZ_CA_EQ``   →  yfinance ``FSZ.TO``
#   • Trailing exchange-letter:   ``NUCGl_EQ``    →  yfinance ``NUCG.L`` (London)
#                                 ``SUp_EQ``      →  yfinance ``SU.PA``  (Paris)
# The exchange letter is LOWERCASE, so any pipeline that upper-cases the code
# before parsing (as our DB ingestion does → ``NUCGL_EQ``) destroys the hint.
# Resolution must therefore run on the ORIGINAL-CASE code from T212 metadata.
# ──────────────────────────────────────────────────────────────────────────

# Two-letter country segment (``_XX_EQ``) → yfinance suffix.
_COUNTRY_SUFFIX: dict[str, str] = {
    "US": "",      # US — bare symbol
    "CA": ".TO",   # Toronto
    "DE": ".DE",   # Deutsche Börse Xetra
    "FR": ".PA",   # Euronext Paris
    "BE": ".BR",   # Euronext Brussels
    "BB": ".BR",
    "AT": ".VI",   # Vienna
    "PT": ".LS",   # Euronext Lisbon
    "AU": ".AX",   # ASX
}

# Trailing lowercase exchange letter → yfinance suffix. London (``l``) and the
# US bare-symbol path are the reliable ones (T212's base symbol matches the
# venue ticker); continental venues are best-effort (T212 short codes often
# diverge from the Xetra/Euronext symbol). Single source of truth for both the
# chart endpoint and the leveraged engine.
_EXCHANGE_LETTER_SUFFIX: dict[str, str] = {
    "l": ".L",    # London Stock Exchange
    "p": ".PA",   # Euronext Paris
    "a": ".AS",   # Euronext Amsterdam
    "m": ".MI",   # Borsa Italiana (Milan)
    "e": ".MC",   # BME (Madrid)
    "s": ".SW",   # SIX Swiss
    "d": ".DE",   # Deutsche Börse Xetra (best-effort)
}

# Currencies that are quoted in minor units (pence) on their venue — yfinance
# returns these LSE lines in GBp, so downstream P&L must divide by 100 to reach
# major-unit (GBP) values.
_MINOR_UNIT_CURRENCIES = {"GBX", "ZAC", "ILA"}


def is_minor_unit_currency(currency_code: str | None) -> bool:
    """True if a venue quotes this currency in minor units (e.g. GBX = pence)."""
    return str(currency_code or "").strip().upper() in _MINOR_UNIT_CURRENCIES


def resolve_yfinance_ticker(ticker: str, currency_code: str | None = None) -> str:
    """Resolve a Trading 212 instrument code (or raw symbol) to a yfinance ticker.

    Case-sensitive: pass the ORIGINAL-case T212 code (e.g. ``NUCGl_EQ``) so the
    lowercase exchange letter survives. ``currency_code`` is accepted for future
    disambiguation but resolution is driven by the code structure, which already
    encodes the venue. Falls back to the static override map and digit-prefix
    heuristic for leveraged LSE short codes.
    """
    raw = str(ticker or "").strip()
    if not raw:
        raise LeveragedMarketError("ticker is required")

    # Already a yfinance ticker (has an exchange suffix)? pass through.
    if "." in raw and "_" not in raw:
        return raw.upper()

    if raw.endswith("_EQ"):
        raw = raw[:-3]

    # Country-segment form: ``BASE_XX`` where XX is a two-letter country code.
    country_match = re.match(r"^(.+)_([A-Z]{2})$", raw)
    if country_match:
        base, country = country_match.group(1), country_match.group(2)
        suffix = _COUNTRY_SUFFIX.get(country)
        if suffix is not None:
            mapped = T212_TO_YFINANCE.get(base.upper())
            return mapped if mapped else f"{base.upper()}{suffix}"
        # Unknown country segment — drop it and keep parsing the base.
        raw = base

    # Trailing exchange-letter form: ``BASE`` (uppercase) + lowercase letter(s)
    # + optional T212 dedupe digit (e.g. ``3ASMl1`` → base ``3ASM``, tail ``l``).
    letter_match = re.match(r"^([0-9A-Z]+?)([a-z]+)\d*$", raw)
    if letter_match:
        base, tail = letter_match.group(1), letter_match.group(2)
        sym = base.upper()
        mapped = T212_TO_YFINANCE.get(sym)
        if mapped:
            return mapped
        suffix = _EXCHANGE_LETTER_SUFFIX.get(tail) or _EXCHANGE_LETTER_SUFFIX.get(tail[-1])
        if suffix:
            return f"{sym}{suffix}"
        return sym

    # No exchange hint: a bare symbol (US-style) or static-mapped short code.
    sym = raw.upper()
    if "." in sym:
        return sym
    mapped = T212_TO_YFINANCE.get(sym)
    if mapped:
        return mapped
    # Many leveraged LSE products are alphanumeric short codes starting with a
    # digit (e.g. ``3NVD`` → ``3NVD.L``).
    if sym and sym[0].isdigit():
        return f"{sym}.L"
    return sym


def _cache_get(cache: dict[Any, tuple[float, Any]], key: Any) -> Any | None:
    now = time.time()
    with _cache_lock:
        payload = cache.get(key)
        if not payload:
            return None
        ts, value = payload
        if now - ts > _CACHE_TTL_SECONDS:
            cache.pop(key, None)
            return None
        return value


def _cache_set(cache: dict[Any, tuple[float, Any]], key: Any, value: Any) -> Any:
    with _cache_lock:
        if len(cache) >= _CACHE_MAX_ITEMS:
            oldest_key = min(cache.items(), key=lambda item: item[1][0])[0]
            cache.pop(oldest_key, None)
        cache[key] = (time.time(), value)
    return value


def to_yfinance_ticker(ticker: str) -> str:
    """Back-compat wrapper around :func:`resolve_yfinance_ticker`.

    Callers that already hold an explicit ``yfinance_ticker`` (resolved upstream
    from original-case T212 metadata) should pass it directly — it round-trips
    cleanly. For raw uppercased codes this still recovers London / US / static
    mappings, but the lowercase exchange letter cannot be recovered once lost,
    so prefer resolving from metadata at the payload layer.
    """
    return resolve_yfinance_ticker(ticker)


def _fetch_via_ticker(yf_ticker: str, period: str, interval: str) -> pd.DataFrame:
    """Fetch history using Ticker.history() which returns a flat DataFrame.

    Unlike yf.download(), Ticker.history() returns simple column names
    (Open, High, Low, Close, Volume) without MultiIndex, making it more
    reliable for single-ticker requests.
    """
    t = yf.Ticker(yf_ticker)
    frame = t.history(
        period=period,
        interval=interval,
        auto_adjust=True,
        timeout=10,
    )
    return frame


def _download_history_frame(ticker: str, period: str, interval: str) -> pd.DataFrame:
    yf_ticker = to_yfinance_ticker(ticker)

    frame = pd.DataFrame()
    last_err: Exception | None = None

    for attempt in range(2):
        try:
            frame = _fetch_via_ticker(yf_ticker, period, interval)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            logger.warning(
                "yfinance attempt %d failed for %s: %s", attempt + 1, yf_ticker, exc
            )
            if attempt == 0:
                _clear_yfinance_cookie_cache()
                time.sleep(0.5)
                continue
            raise LeveragedMarketError(
                f"yfinance request failed for {yf_ticker}: {exc}"
            ) from exc

        if not frame.empty:
            break

        # Empty frame on first attempt -- clear cookies and retry.
        if attempt == 0:
            logger.warning(
                "yfinance returned empty frame for %s, clearing cookie cache and retrying",
                yf_ticker,
            )
            _clear_yfinance_cookie_cache()
            time.sleep(0.5)

    if frame.empty:
        msg = f"No price history for {ticker} ({yf_ticker})"
        if last_err:
            msg += f" (last error: {last_err})"
        raise LeveragedMarketError(msg)

    df = frame.reset_index()
    # Ticker.history() returns flat columns but guard against MultiIndex
    # in case a future yfinance version changes behaviour.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[0]).lower() for col in df.columns]
    else:
        df.columns = [str(col).lower() for col in df.columns]

    if "date" not in df.columns:
        df = df.rename(columns={df.columns[0]: "date"})

    keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
    out = df[keep].copy()
    out["date"] = pd.to_datetime(out["date"], utc=True, errors="coerce")
    out = out.dropna(subset=["date", "close"]).reset_index(drop=True)
    if out.empty:
        raise LeveragedMarketError(f"No valid candles for {ticker}")
    return out


def get_price_history(ticker: str, period: str = "3mo", interval: str = "1d") -> list[dict[str, Any]]:
    key = (ticker.upper().strip(), period, interval)
    cached = _cache_get(_history_cache, key)
    if cached is not None:
        return cached

    df = _download_history_frame(ticker, period, interval)
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        rows.append(
            {
                "date": row["date"].isoformat(),
                "open": float(row.get("open", 0.0) or 0.0),
                "high": float(row.get("high", 0.0) or 0.0),
                "low": float(row.get("low", 0.0) or 0.0),
                "close": float(row.get("close", 0.0) or 0.0),
                "volume": float(row.get("volume", 0.0) or 0.0),
            }
        )

    return _cache_set(_history_cache, key, rows)


# yfinance exchange suffix → venue quote currency. ``.L`` (LSE) is the common
# trap: most London ETPs/equities are quoted in GBX (pence), NOT GBP or USD —
# treating them as USD silently mis-scales P&L in the ISA by ~100x and the FX
# leg. When the caller knows the authoritative T212 ``currencyCode`` it should
# pass it via ``currency_code``; otherwise we infer from the suffix.
_YF_SUFFIX_CURRENCY: dict[str, str] = {
    ".L": "GBX",   # London — pence (minor unit); see is_minor_unit_currency
    ".DE": "EUR",
    ".PA": "EUR",
    ".AS": "EUR",
    ".MI": "EUR",
    ".MC": "EUR",
    ".BR": "EUR",
    ".VI": "EUR",
    ".LS": "EUR",
    ".SW": "CHF",
    ".TO": "CAD",
    ".AX": "AUD",
}


def infer_currency(yf_ticker: str) -> str:
    """Best-effort venue currency from a yfinance ticker suffix (USD default)."""
    sym = str(yf_ticker or "").upper()
    if "." in sym:
        suffix = "." + sym.rsplit(".", 1)[-1]
        return _YF_SUFFIX_CURRENCY.get(suffix, "USD")
    return "USD"


def get_price(ticker: str, currency_code: str | None = None) -> dict[str, Any]:
    key = ticker.upper().strip()
    cached = _cache_get(_price_cache, key)
    if cached is not None:
        return cached

    # 5d window gives us current + previous close for change.
    candles = get_price_history(ticker, period="5d", interval="1d")
    if not candles:
        raise LeveragedMarketError(f"No candles for {ticker}")

    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else last
    last_close = float(last.get("close", 0.0) or 0.0)
    prev_close = float(prev.get("close", 0.0) or 0.0)
    change_pct = 0.0
    if prev_close > 0:
        change_pct = (last_close / prev_close) - 1.0

    yf_ticker = to_yfinance_ticker(ticker)
    # Authoritative T212 currency wins; else infer from the venue suffix.
    currency = (currency_code or "").strip().upper() or infer_currency(yf_ticker)

    payload = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": yf_ticker,
        "price": last_close,
        "currency": currency,
        "is_minor_unit": is_minor_unit_currency(currency),
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "change_pct": change_pct,
    }
    return _cache_set(_price_cache, key, payload)


@dataclass
class _Tech:
    rsi_14: float | None
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    macd: float | None
    macd_signal: float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    atr_14: float | None
    trend_direction: str


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _compute_technicals(df: pd.DataFrame) -> _Tech:
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta.clip(upper=0))
    avg_gain = gains.rolling(window=14, min_periods=14).mean()
    avg_loss = losses.rolling(window=14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))

    sma20 = close.rolling(window=20, min_periods=20).mean()
    sma50 = close.rolling(window=50, min_periods=50).mean()
    sma200 = close.rolling(window=200, min_periods=200).mean()

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()

    std20 = close.rolling(window=20, min_periods=20).std()
    boll_upper = sma20 + 2 * std20
    boll_lower = sma20 - 2 * std20

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = tr.rolling(window=14, min_periods=14).mean()

    last_close = _safe_float(close.iloc[-1])
    last_sma50 = _safe_float(sma50.iloc[-1])
    last_sma200 = _safe_float(sma200.iloc[-1])

    trend = "mixed"
    if last_close is not None and last_sma50 is not None and last_sma200 is not None:
        if last_close > last_sma50 > last_sma200:
            trend = "uptrend"
        elif last_close < last_sma50 < last_sma200:
            trend = "downtrend"

    return _Tech(
        rsi_14=_safe_float(rsi.iloc[-1]),
        sma_20=_safe_float(sma20.iloc[-1]),
        sma_50=_safe_float(sma50.iloc[-1]),
        sma_200=_safe_float(sma200.iloc[-1]),
        macd=_safe_float(macd.iloc[-1]),
        macd_signal=_safe_float(macd_signal.iloc[-1]),
        bollinger_upper=_safe_float(boll_upper.iloc[-1]),
        bollinger_lower=_safe_float(boll_lower.iloc[-1]),
        atr_14=_safe_float(atr14.iloc[-1]),
        trend_direction=trend,
    )


def get_technicals(ticker: str, period: str = "6mo") -> dict[str, Any]:
    key = (ticker.upper().strip(), period)
    cached = _cache_get(_technical_cache, key)
    if cached is not None:
        return cached

    df = _download_history_frame(ticker, period=period, interval="1d")
    tech = _compute_technicals(df)
    price = float(df["close"].iloc[-1])

    payload = {
        "ticker": ticker.upper().strip(),
        "yfinance_ticker": to_yfinance_ticker(ticker),
        "price": price,
        "rsi_14": tech.rsi_14,
        "sma_20": tech.sma_20,
        "sma_50": tech.sma_50,
        "sma_200": tech.sma_200,
        "macd": tech.macd,
        "macd_signal": tech.macd_signal,
        "bollinger_upper": tech.bollinger_upper,
        "bollinger_lower": tech.bollinger_lower,
        "atr_14": tech.atr_14,
        "trend_direction": tech.trend_direction,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
    }

    return _cache_set(_technical_cache, key, payload)
