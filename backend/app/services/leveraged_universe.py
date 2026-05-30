"""Market-driven leveraged universe.

Builds the candidate set the leveraged engine should look at *today* from the
LIVE Trading 212 instrument metadata — not a hand-curated list. Two layers:

1. ``build_underlying_map`` inverts the leveraged registry into
   ``{underlying: {long, inverse}}`` so any underlying can be expressed in
   either direction via the correct ETP (3x long for upside, 3x inverse for
   ISA-only downside). This retires the hardcoded ``_ETP_UNDERLYING`` map.

2. ``build_universe`` ranks underlyings by the strength of their recent move
   (the underlying's own trend/momentum), picks the natural direction, then
   **gates by market regime** (suppress longs in risk-off, inverse in risk-on)
   and maps each to its tradeable ETP. The result is an ordered watchlist the
   morning scan / daily-alpha loop can act on.

All market data flows through ``leveraged_market`` (which raises rather than
fabricating), so an outage yields fewer ranked names, never fake ones.
"""

from __future__ import annotations

import logging
import re
import time
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from app.services.leveraged_market import LeveragedMarketError, get_technicals
from app.services.leveraged_registry import build_leveraged_registry
from app.services.regime_service import RegimeState, compute_regime

logger = logging.getLogger(__name__)

_MAP_TTL_SECONDS = 6 * 3600
_UNIVERSE_TTL_SECONDS = 600
_lock = Lock()
_map_cache: tuple[float, dict[str, dict[str, Any]]] | None = None
_universe_cache: tuple[float, dict[str, Any]] | None = None

# Canonical underlying NAME → ticker. Many Leverage Shares names carry no
# trailing ticker token (e.g. "3x Long NVIDIA"), so classify_leveraged returns
# underlying_ticker=None and the name would key separately from the "…NVDA"
# variant — splitting the long/inverse pair. Canonicalising the common names to
# their ticker re-unites the pair (and gives a yfinance proxy for free).
_CANONICAL_UNDERLYING: dict[str, str] = {
    "NVIDIA": "NVDA", "APPLE": "AAPL", "AMAZON": "AMZN", "ALPHABET": "GOOGL",
    "GOOGLE": "GOOGL", "MICROSOFT": "MSFT", "TESLA": "TSLA", "META": "META",
    "META PLATFORMS": "META", "NETFLIX": "NFLX", "PALANTIR": "PLTR",
    "COINBASE": "COIN", "ALIBABA": "BABA", "BAIDU": "BIDU", "BOEING": "BA",
    "BROADCOM": "AVGO", "MICRON": "MU", "MICRON TECHNOLOGY": "MU",
    "INTEL": "INTC", "AMD": "AMD", "ADVANCED MICRO DEVICES": "AMD",
    "TAIWAN SEMICONDUCTOR": "TSM", "ASML": "ASML", "BARCLAYS": "BARC.L",
    "BP": "BP.L", "ASTRAZENECA": "AZN.L", "BAE SYSTEMS": "BA.L",
    "DIAGEO": "DGE.L", "RIVIAN": "RIVN", "COREWEAVE": "CRWV", "SANDISK": "SNDK",
    "NEBIUS": "NBIS", "UBER": "UBER", "MICROSTRATEGY": "MSTR", "STRATEGY": "MSTR",
}

# Index/commodity underlyings whose classified name lacks a clean equity ticker
# get a tradeable yfinance proxy so we can still read their trend.
_PROXY_OVERRIDES: dict[str, str] = {
    "S&P 500": "SPY",
    "SP500": "SPY",
    "NASDAQ 100": "QQQ",
    "NASDAQ100": "QQQ",
    "FTSE 100": "^FTSE",
    "GOLD": "GLD",
    "SILVER": "SLV",
    "GERMANY 40": "^GDAXI",
    "DAX": "^GDAXI",
    "BRENT CRUDE OIL": "BZ=F",
    "COPPER": "HG=F",
}


def _underlying_key(entry: dict[str, Any]) -> str | None:
    """Stable key for an underlying: prefer its ticker, else a canonical/name slug."""
    tkr = (entry.get("underlying_ticker") or "").strip().upper()
    if tkr:
        return tkr
    name = re.sub(r"\s+", " ", (entry.get("underlying_name") or "").strip().upper())
    if not name:
        return None
    # Canonicalise common names to their ticker so long/inverse variants unite.
    return _CANONICAL_UNDERLYING.get(name, name)


def _is_isa_tradeable(entry: dict[str, Any]) -> bool:
    """London (.L / GBX/GBP/USD lines) leveraged ETPs are ISA-tradeable."""
    cur = str(entry.get("currency") or "").upper()
    # The vast majority of Leverage Shares ISA ETPs list on the LSE; any GBX/GBP
    # line is ISA-eligible, and the USD London lines too. We can't see the
    # exchange directly, but currency + leveraged classification is a good proxy.
    return cur in {"GBX", "GBP", "USD", "EUR"}


def _proxy_for(entry: dict[str, Any]) -> str | None:
    """A yfinance symbol to read the underlying's trend from."""
    tkr = (entry.get("underlying_ticker") or "").strip().upper()
    if tkr:
        return tkr
    name = re.sub(r"\s+", " ", (entry.get("underlying_name") or "").strip().upper())
    return _CANONICAL_UNDERLYING.get(name) or _PROXY_OVERRIDES.get(name)


def _factor_rank(etp: dict[str, Any]) -> tuple[int, float]:
    """Preference key for choosing a product: 3x first, then lower leverage.

    Returns (is_three_x, -factor) — so factor==3 wins, and among non-3x the
    LOWER factor (less decay risk) is preferred over a higher one.
    """
    try:
        f = float(etp.get("factor") or 0)
    except (TypeError, ValueError):
        f = 0.0
    return (1 if f == 3 else 0, -f)


def build_underlying_map(instruments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Invert the leveraged registry to ``{underlying: {long, inverse, proxy}}``.

    Pure transform — feed it a T212 instrument-metadata list. For each direction
    we keep the highest-leverage ISA-tradeable ETP (ties → first seen).
    """
    registry = build_leveraged_registry(instruments)
    out: dict[str, dict[str, Any]] = {}
    for entry in registry.values():
        key = _underlying_key(entry)
        if not key:
            continue
        if not _is_isa_tradeable(entry):
            continue
        direction = "inverse" if entry.get("direction") == "inverse" else "long"
        slot = out.setdefault(
            key,
            {
                "underlying": key,
                "underlying_name": entry.get("underlying_name"),
                "proxy": _proxy_for(entry),
                "long": None,
                "inverse": None,
            },
        )
        # Pick the product per direction. Prefer the documented 3x strategy;
        # fall back to whatever leverage is available (2x, 5x). 5x daily ETPs
        # carry severe path-decay, so we never surface them over a 3x.
        existing = slot[direction]
        cand = {
            "ticker": entry["ticker"],
            "name": entry["name"],
            "factor": entry.get("factor"),
            "currency": entry.get("currency"),
        }
        if existing is None or _factor_rank(cand) > _factor_rank(existing):
            slot[direction] = cand
        if not slot.get("proxy"):
            slot["proxy"] = _proxy_for(entry)
    return out


def _load_instruments(db: Session) -> list[dict[str, Any]]:
    from app.services.config_store import ConfigStore
    from app.services.t212_client import build_t212_client

    store = ConfigStore(db)
    last_err: Exception | None = None
    for acct in ("invest", "stocks_isa"):
        try:
            client = build_t212_client(store, acct)
            return list(client.get_instruments_metadata())
        except Exception as exc:  # noqa: BLE001 — try the other account's creds
            last_err = exc
            continue
    # Both accounts failed — surface it (callers treat empty as degraded, not
    # as a legitimately empty market) instead of swallowing it silently.
    logger.warning("leveraged universe: could not load T212 instruments: %s", last_err)
    return []


def get_underlying_map(db: Session, force: bool = False) -> dict[str, dict[str, Any]]:
    global _map_cache
    with _lock:
        if not force and _map_cache and (time.time() - _map_cache[0]) < _MAP_TTL_SECONDS:
            return _map_cache[1]
    mapping = build_underlying_map(_load_instruments(db))
    # Only cache a NON-EMPTY map: a transient T212 outage returns {} and must
    # not poison the cache (and starve the universe) for the full TTL. Stamp at
    # completion time so the TTL reflects when the data was actually read.
    if mapping:
        with _lock:
            _map_cache = (time.time(), mapping)
    return mapping


def _move_score(tech: dict[str, Any]) -> tuple[float, str]:
    """Return (signed strength in ~[-1,1], trend label) from an underlying's tech."""
    trend = str(tech.get("trend_direction") or "mixed")
    price = float(tech.get("price") or 0.0)
    sma20 = float(tech.get("sma_20") or 0.0)
    sma50 = float(tech.get("sma_50") or 0.0)
    rsi = float(tech.get("rsi_14") or 50.0)
    score = 0.0
    if trend == "uptrend":
        score += 0.5
    elif trend == "downtrend":
        score -= 0.5
    if sma20 > 0 and price > 0:
        score += 0.25 if price > sma20 else -0.25
    if sma50 > 0 and sma20 > 0:
        score += 0.15 if sma20 > sma50 else -0.15
    # RSI distance from 50 adds conviction in the trend's direction.
    score += (rsi - 50.0) / 100.0
    return max(-1.0, min(1.0, score)), trend


def build_universe(
    db: Session,
    regime: RegimeState | None = None,
    *,
    candidates: list[str] | None = None,
    top_n: int = 8,
    max_eval: int = 24,
) -> dict[str, Any]:
    """Rank underlyings by move strength, pick direction, gate by regime, map to ETP.

    Returns ``{regime, ranked: [...], evaluated, available_underlyings, errors}``.
    Cached ~10 min (keyed only by the default-candidate path).
    """
    global _universe_cache
    use_cache = candidates is None
    now = time.time()
    if use_cache:
        with _lock:
            if _universe_cache and (now - _universe_cache[0]) < _UNIVERSE_TTL_SECONDS:
                return _universe_cache[1]

    regime = regime or compute_regime()
    umap = get_underlying_map(db)

    # Default candidate pool: underlyings that can be expressed in BOTH directions
    # (so the regime gate has something to pick), with a readable proxy. This is
    # exactly the set where a regime tilt is actionable.
    if candidates is None:
        pool = [
            k for k, v in umap.items()
            if v.get("proxy") and (v.get("long") or v.get("inverse"))
        ]
        # Prefer those with both directions available, then cap the eval budget.
        pool.sort(key=lambda k: (umap[k].get("long") is not None and umap[k].get("inverse") is not None), reverse=True)
        pool = pool[:max_eval]
    else:
        pool = [c.strip().upper() for c in candidates if c.strip()]

    ranked: list[dict[str, Any]] = []
    errors: list[str] = []
    for key in pool:
        slot = umap.get(key)
        if not slot or not slot.get("proxy"):
            continue
        try:
            tech = get_technicals(slot["proxy"], period="6mo")
        except LeveragedMarketError as exc:
            errors.append(f"{key}: {exc}")
            continue
        score, trend = _move_score(tech)
        direction = "long" if score >= 0 else "inverse"
        etp = slot.get(direction)
        if etp is None:
            # No ETP for the natural direction — skip (can't express it in ISA).
            continue
        aligned = regime.favours("long" if direction == "long" else "short")
        # In a strong opposing regime, drop the name entirely.
        if not aligned and abs(regime.score) >= 0.6 and regime.regime != "neutral":
            continue
        ranked.append({
            "underlying": key,
            "underlying_name": slot.get("underlying_name"),
            "direction": direction,
            "etp_ticker": etp["ticker"],
            "etp_name": etp["name"],
            "factor": etp.get("factor"),
            "currency": etp.get("currency"),
            "move_score": round(score, 3),
            "trend": trend,
            "regime_aligned": aligned,
        })

    # Strongest conviction first; aligned names ahead of merely-allowed ones.
    ranked.sort(key=lambda r: (r["regime_aligned"], abs(r["move_score"])), reverse=True)
    ranked = ranked[:top_n]

    # Distinguish "no instruments could be loaded" (T212 outage) from a
    # legitimately empty result, so the caller/UI doesn't read an outage as a
    # quiet market.
    degraded = not umap
    result = {
        "regime": regime.to_dict(),
        "ranked": ranked,
        "evaluated": len(pool),
        "available_underlyings": len(umap),
        "degraded": degraded,
        "error_reason": "could not load T212 instrument metadata" if degraded else None,
        "errors": errors,
    }
    # Don't cache a degraded result (stamp at completion time when we do cache).
    if use_cache and not degraded:
        with _lock:
            _universe_cache = (time.time(), result)
    return result
