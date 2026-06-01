"""Market-intelligence service — news (Finnhub) + macro (FRED).

The data layer for the "Sense" loop. Keyless-degradable by design:
- API keys come from the environment (``FINNHUB_API_KEY`` / ``FRED_API_KEY``,
  passed to MCP subprocesses by ``claude_sdk_config.resolve_intel_env``) or, for
  in-process callers, from ConfigStore ``data_providers``.
- If the Finnhub key is missing, company news falls back to yfinance (Yahoo).
- If the FRED key is missing, the macro snapshot is simply empty.
Never raises on a provider outage — returns what it can so the brief/watches
degrade gracefully rather than crash.

Stable contract: these functions are what the ``intel`` MCP tools and the
deterministic watches both call, so the provider behind them can change (e.g.
swap to an OpenBB sidecar) without touching agents or the loop.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_FINNHUB = "https://finnhub.io/api/v1"
_FRED = "https://api.stlouisfed.org/fred"

# Light TTL caches so a brief + several watches in the same window don't re-hit
# the providers (Finnhub free = 60 req/min).
_TTL_NEWS = 300       # 5 min
_TTL_MACRO = 3600     # 1 h
_TTL_EARN = 6 * 3600  # 6 h
_lock = Lock()
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, ttl: int, producer):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    value = producer()
    with _lock:
        _cache[key] = (now, value)
    return value


def _key(env_name: str, cfg_key: str) -> str:
    v = os.environ.get(env_name, "").strip()
    if v:
        return v
    try:
        from app.core.database import SessionLocal
        from app.services.config_store import ConfigStore

        with SessionLocal() as db:
            return str(ConfigStore(db).get("data_providers", {}).get(cfg_key, "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def finnhub_key() -> str:
    return _key("FINNHUB_API_KEY", "finnhub_api_key")


def fred_key() -> str:
    return _key("FRED_API_KEY", "fred_api_key")


def providers_status() -> dict[str, bool]:
    """Which providers are configured (for diagnostics / graceful messaging)."""
    return {"finnhub": bool(finnhub_key()), "fred": bool(fred_key())}


# ── News ────────────────────────────────────────────────────────────────────

def _norm_finnhub(item: dict[str, Any], ticker: str | None = None) -> dict[str, Any]:
    ts = int(item.get("datetime") or 0)
    return {
        "id": str(item.get("id") or item.get("url") or ""),
        "datetime": datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None,
        "ts": ts,
        "headline": (item.get("headline") or "").strip(),
        "summary": (item.get("summary") or "").strip(),
        "source": item.get("source") or "",
        "url": item.get("url") or "",
        "ticker": ticker,
    }


def _yfinance_news(ticker: str, limit: int) -> list[dict[str, Any]]:
    """Fallback when no Finnhub key — Yahoo news via yfinance."""
    try:
        import yfinance as yf

        out: list[dict[str, Any]] = []
        for it in (yf.Ticker(ticker).news or [])[:limit]:
            c = it.get("content", it) if isinstance(it, dict) else {}
            prov = c.get("provider") if isinstance(c.get("provider"), dict) else {}
            url = (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else it.get("link")
            out.append({
                "id": str(it.get("id") or c.get("id") or url or ""),
                "datetime": c.get("pubDate") or None,
                "ts": int(it.get("providerPublishTime") or 0),
                "headline": (c.get("title") or it.get("title") or "").strip(),
                "summary": (c.get("summary") or "").strip(),
                "source": prov.get("displayName") or it.get("publisher") or "Yahoo",
                "url": url or "",
                "ticker": ticker,
            })
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance news fallback failed for %s: %s", ticker, exc)
        return []


def get_company_news(ticker: str, since_days: int = 3, limit: int = 20) -> list[dict[str, Any]]:
    """Recent news for one ticker, newest first. Finnhub → yfinance fallback."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return []

    def _produce() -> list[dict[str, Any]]:
        key = finnhub_key()
        if not key:
            return _yfinance_news(ticker, limit)
        today = date.today()
        frm = today - timedelta(days=max(1, since_days))
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(f"{_FINNHUB}/company-news", params={
                    "symbol": ticker, "from": frm.isoformat(), "to": today.isoformat(), "token": key,
                })
            if r.status_code != 200:
                logger.warning("finnhub company-news %s: HTTP %s", ticker, r.status_code)
                return _yfinance_news(ticker, limit)
            items = [_norm_finnhub(it, ticker) for it in r.json() if isinstance(it, dict)]
            items.sort(key=lambda x: x["ts"], reverse=True)
            return items[:limit]
        except Exception as exc:  # noqa: BLE001
            logger.warning("finnhub company-news %s failed: %s", ticker, exc)
            return _yfinance_news(ticker, limit)

    return _cached(f"cnews:{ticker}:{since_days}:{limit}", _TTL_NEWS, _produce)


def get_market_news(limit: int = 20) -> list[dict[str, Any]]:
    """General market/world news (Finnhub 'general' category)."""
    def _produce() -> list[dict[str, Any]]:
        key = finnhub_key()
        if not key:
            return []
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(f"{_FINNHUB}/news", params={"category": "general", "token": key})
            if r.status_code != 200:
                return []
            items = [_norm_finnhub(it) for it in r.json() if isinstance(it, dict)]
            items.sort(key=lambda x: x["ts"], reverse=True)
            return items[:limit]
        except Exception as exc:  # noqa: BLE001
            logger.warning("finnhub market-news failed: %s", exc)
            return []

    return _cached(f"mnews:{limit}", _TTL_NEWS, _produce)


# ── Macro (FRED) ──────────────────────────────────────────────────────────────

# Curated, high-signal series — the ones that actually move a portfolio.
_MACRO_SERIES: list[tuple[str, str, str]] = [
    ("DGS10", "US 10Y yield", "%"),
    ("DGS2", "US 2Y yield", "%"),
    ("T10Y2Y", "10Y–2Y spread", "%"),
    ("VIXCLS", "VIX", ""),
    ("DFF", "Fed funds rate", "%"),
    ("DEXUSUK", "USD/GBP", ""),
]


def get_macro_snapshot() -> dict[str, Any]:
    """Latest value + prior for the curated FRED series. Empty if no FRED key."""
    def _produce() -> dict[str, Any]:
        key = fred_key()
        if not key:
            return {"series": [], "fred": False}
        out: list[dict[str, Any]] = []
        try:
            with httpx.Client(timeout=15) as c:
                for sid, label, unit in _MACRO_SERIES:
                    r = c.get(f"{_FRED}/series/observations", params={
                        "series_id": sid, "api_key": key, "file_type": "json",
                        "sort_order": "desc", "limit": 5,
                    })
                    if r.status_code != 200:
                        continue
                    obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
                    if not obs:
                        continue
                    latest = obs[0]
                    prior = obs[1] if len(obs) > 1 else None
                    try:
                        lv = float(latest["value"])
                        pv = float(prior["value"]) if prior else None
                    except (TypeError, ValueError):
                        continue
                    out.append({
                        "id": sid, "label": label, "unit": unit,
                        "value": lv, "date": latest["date"],
                        "prev": pv, "change": (round(lv - pv, 4) if pv is not None else None),
                    })
        except Exception as exc:  # noqa: BLE001
            logger.warning("FRED snapshot failed: %s", exc)
        return {"series": out, "fred": True}

    return _cached("macro", _TTL_MACRO, _produce)


# ── Earnings (Finnhub) ────────────────────────────────────────────────────────

def get_earnings(ticker: str) -> dict[str, Any]:
    """Next earnings date (forward 90d) + recent surprise history for a ticker."""
    ticker = (ticker or "").strip().upper()

    def _produce() -> dict[str, Any]:
        key = finnhub_key()
        if not ticker or not key:
            return {"ticker": ticker, "next": None, "surprises": []}
        today = date.today()
        out: dict[str, Any] = {"ticker": ticker, "next": None, "surprises": []}
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(f"{_FINNHUB}/calendar/earnings", params={
                    "symbol": ticker, "from": today.isoformat(),
                    "to": (today + timedelta(days=90)).isoformat(), "token": key,
                })
                if r.status_code == 200:
                    rows = sorted(r.json().get("earningsCalendar", []), key=lambda x: x.get("date", ""))
                    if rows:
                        nxt = rows[0]
                        out["next"] = {
                            "date": nxt.get("date"),
                            "days_away": (date.fromisoformat(nxt["date"]) - today).days if nxt.get("date") else None,
                            "hour": nxt.get("hour"),
                        }
                # recent surprises
                r2 = c.get(f"{_FINNHUB}/stock/earnings", params={"symbol": ticker, "limit": 4, "token": key})
                if r2.status_code == 200:
                    out["surprises"] = [
                        {"period": e.get("period"), "actual": e.get("actual"),
                         "estimate": e.get("estimate"), "surprise_pct": e.get("surprisePercent")}
                        for e in (r2.json() or []) if isinstance(e, dict)
                    ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("finnhub earnings %s failed: %s", ticker, exc)
        return out

    return _cached(f"earn:{ticker}", _TTL_EARN, _produce)
