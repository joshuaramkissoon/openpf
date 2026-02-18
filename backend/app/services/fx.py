from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import Lock

import httpx

_CACHE_TTL = timedelta(minutes=30)
_cache_lock = Lock()
_rate_cache: dict[tuple[str, str], tuple[float, datetime]] = {}

_FALLBACK_RATES: dict[tuple[str, str], float] = {
    ("USD", "GBP"): 0.79,
    ("GBP", "USD"): 1.27,
    ("EUR", "GBP"): 0.86,
    ("GBP", "EUR"): 1.16,
    ("EUR", "USD"): 1.10,
    ("USD", "EUR"): 0.91,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _from_cache(base: str, quote: str) -> float | None:
    key = (base, quote)
    with _cache_lock:
        cached = _rate_cache.get(key)
        if not cached:
            return None
        rate, expires = cached
        if _now() >= expires:
            _rate_cache.pop(key, None)
            return None
        return rate


def _set_cache(base: str, quote: str, rate: float) -> None:
    key = (base, quote)
    with _cache_lock:
        _rate_cache[key] = (rate, _now() + _CACHE_TTL)


def _fallback_rate(base: str, quote: str) -> float:
    direct = _FALLBACK_RATES.get((base, quote))
    if direct:
        return direct
    inverse = _FALLBACK_RATES.get((quote, base))
    if inverse and inverse > 0:
        return 1.0 / inverse
    return 1.0


def get_fx_rate(base_currency: str, quote_currency: str) -> float:
    base = (base_currency or "").upper().strip() or "USD"
    quote = (quote_currency or "").upper().strip() or "USD"
    if base == quote:
        return 1.0

    cached = _from_cache(base, quote)
    if cached is not None:
        return cached

    try:
        response = httpx.get(
            "https://api.frankfurter.app/latest",
            params={"from": base, "to": quote},
            timeout=5.0,
        )
        if response.status_code == 200:
            payload = response.json()
            rate = float((payload.get("rates") or {}).get(quote) or 0.0)
            if rate > 0:
                _set_cache(base, quote, rate)
                return rate
    except Exception:
        pass

    fallback = _fallback_rate(base, quote)
    _set_cache(base, quote, fallback)
    return fallback
