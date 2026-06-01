"""Backfilled daily FX (GBP↔USD) for point-in-time portfolio conversion.

The spot ``app.services.fx.get_fx_rate`` answers "what's it worth now". The
equity/return curve needs the opposite: convert each *historical* point at the
rate that held on its own date, so the blended GBP curve doesn't drift with
today's exchange rate. We cache a single canonical series — USD per 1 GBP, daily
(FRED ``DEXUSUK``) — and serve nearest-prior lookups. yfinance ``GBPUSD=X`` is the
keyless fallback; spot is the last resort for dates/pairs we can't cover.
"""

from __future__ import annotations

import bisect
import logging
from datetime import date, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FxRateDaily
from app.services.fx import get_fx_rate
from app.services.intel_service import fred_key

logger = logging.getLogger(__name__)

_FRED = "https://api.stlouisfed.org/fred"


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


# ── Backfill ──────────────────────────────────────────────────────────────────

def _fetch_fred(start: date, end: date) -> dict[date, float]:
    key = fred_key()
    if not key:
        return {}
    out: dict[date, float] = {}
    try:
        with httpx.Client(timeout=20) as c:
            r = c.get(f"{_FRED}/series/observations", params={
                "series_id": "DEXUSUK", "api_key": key, "file_type": "json",
                "observation_start": start.isoformat(), "observation_end": end.isoformat(),
            })
            if r.status_code != 200:
                logger.warning("FRED DEXUSUK fetch failed: %s", r.status_code)
                return {}
            for o in r.json().get("observations", []):
                d = _parse_date(o.get("date", ""))
                val = o.get("value")
                if d is None or val in (".", "", None):
                    continue
                try:
                    out[d] = float(val)
                except (TypeError, ValueError):
                    continue
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED DEXUSUK fetch error: %s", exc)
    return out


def _fetch_yfinance(start: date, end: date) -> dict[date, float]:
    """Fallback: GBPUSD=X daily closes (USD per GBP)."""
    try:
        import yfinance as yf

        hist = yf.Ticker("GBPUSD=X").history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
        out: dict[date, float] = {}
        for idx, row in hist.iterrows():
            d = idx.date() if hasattr(idx, "date") else None
            close = float(row.get("Close")) if row.get("Close") is not None else None
            if d is not None and close and close > 0:
                out[d] = close
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance GBPUSD=X fallback failed: %s", exc)
        return {}


def ensure_history(db: Session, start: date, end: date) -> int:
    """Cache USD/GBP daily rates covering [start, end]. Idempotent; only fetches
    when the cache doesn't already span the window. Returns rows inserted."""
    existing = db.execute(
        select(FxRateDaily.date).where(FxRateDaily.date >= start, FxRateDaily.date <= end)
    ).scalars().all()
    have = set(existing)
    # FRED has no weekend/holiday prints (and publishes ~a week in arrears), so we
    # never expect every calendar day nor a rate right up to `today`. Skip the
    # fetch when we already hold an anchor at/just-before `start` AND we refreshed
    # the series recently — the time guard is what stops re-hitting FRED on every
    # /history call, since "latest available" is structurally always behind today.
    has_anchor = bool(db.execute(
        select(FxRateDaily.date).where(FxRateDaily.date <= start).order_by(FxRateDaily.date.desc()).limit(1)
    ).scalar_one_or_none())
    last_fetch = db.execute(select(FxRateDaily.fetched_at).order_by(FxRateDaily.fetched_at.desc()).limit(1)).scalar_one_or_none()
    refreshed_recently = last_fetch is not None and (datetime.utcnow() - last_fetch) < timedelta(hours=12)
    if has_anchor and refreshed_recently and len(have) > 0:
        return 0

    rates = _fetch_fred(start, end)
    source = "fred"
    if not rates:
        rates = _fetch_yfinance(start, end)
        source = "yfinance"
    if not rates:
        return 0

    inserted = 0
    for d, rate in sorted(rates.items()):
        if d in have:
            continue
        db.add(FxRateDaily(date=d, usd_per_gbp=rate, source=source))
        inserted += 1
    if inserted:
        db.commit()
    return inserted


# ── Lookup ──────────────────────────────────────────────────────────────────

class FxHistory:
    """Preloaded USD/GBP series with nearest-prior lookup. Build once per request
    set (``load_fx_history``) and reuse across many point conversions."""

    def __init__(self, rows: list[tuple[date, float]]):
        rows = sorted(rows)
        self._dates = [r[0] for r in rows]
        self._rates = [r[1] for r in rows]

    def usd_per_gbp_on(self, on: date) -> float | None:
        if not self._dates:
            return None
        i = bisect.bisect_right(self._dates, on)
        if i == 0:
            return self._rates[0]  # before our history starts — use earliest known
        return self._rates[i - 1]  # nearest prior (handles weekends/holidays)

    def rate(self, base: str, quote: str, on: date) -> float:
        b = (base or "").upper().strip() or "USD"
        q = (quote or "").upper().strip() or "USD"
        if b == q:
            return 1.0
        pair = {b, q}
        if pair == {"GBP", "USD"}:
            r = self.usd_per_gbp_on(on)
            if r and r > 0:
                return r if (b == "GBP" and q == "USD") else 1.0 / r
        # No history for this pair/date — fall back to spot.
        return get_fx_rate(b, q)


def load_fx_history(db: Session) -> FxHistory:
    rows = db.execute(select(FxRateDaily.date, FxRateDaily.usd_per_gbp)).all()
    return FxHistory([(d, r) for d, r in rows])
