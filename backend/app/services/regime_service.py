"""Shared market-regime service.

Promotes the old narrative-only `agent_service._market_regime()` (which read
SPY's signal and returned a bare string that was *displayed* but never fed back
into signal generation) into a structured, reusable `RegimeState` that the
leveraged engine, the autonomous alpha loop, and the dashboard all consume.

A regime is computed from broad-market technicals — SPY and QQQ versus their
SMA50/SMA200 plus trend, combined with the VIX level — and produces a numeric
score in [-1, +1] plus long/inverse biases. Risk-on tilts toward 3x **long**
ETPs; risk-off tilts toward 3x **inverse** ETPs (the only sanctioned downside
path on T212, ISA-only). Neutral keeps both but smaller.

Pure with respect to its inputs: market data is fetched through
`leveraged_market` (which raises rather than fabricating), so a data outage
degrades to a `neutral` regime with an explicit reason — never a fake reading.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

from app.services.leveraged_market import (
    LeveragedMarketError,
    get_price,
    get_technicals,
)

logger = logging.getLogger(__name__)

# Broad-market proxies whose trend defines the regime.
_INDEX_SYMBOLS: tuple[str, ...] = ("SPY", "QQQ")
_VIX_SYMBOL = "^VIX"

_CACHE_TTL_SECONDS = 600  # 10 minutes — regime moves slowly intraday
_cache_lock = Lock()
_cache: tuple[float, "RegimeState"] | None = None

# Score thresholds for classification.
_RISK_ON_AT = 0.30
_RISK_OFF_AT = -0.30
# A VIX at/above this level forces a defensive read regardless of trend.
_VIX_STRESS = 30.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class RegimeState:
    """Structured market-regime read consumed across the app."""

    regime: str                      # 'risk_on' | 'neutral' | 'risk_off'
    label: str                       # human-readable, e.g. 'Risk-on'
    score: float                     # -1 (max risk-off) .. +1 (max risk-on)
    long_bias: float                 # 0..1 — how much to favour 3x long ETPs
    inverse_bias: float              # 0..1 — how much to favour 3x inverse ETPs
    vix: float | None
    vix_state: str                   # 'calm'|'normal'|'elevated'|'stressed'|'unknown'
    breadth: float | None            # 0..1 fraction of index proxies in uptrend
    rationale: str
    components: dict[str, Any] = field(default_factory=dict)
    as_of: str = ""
    stale: bool = False              # True when computed from degraded/partial data

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def favours(self, direction: str) -> bool:
        """Is `direction` ('long'|'short'/'inverse') aligned with the regime?"""
        d = (direction or "").lower()
        is_inverse = d in {"short", "inverse", "down", "bear"}
        if self.regime == "risk_on":
            return not is_inverse
        if self.regime == "risk_off":
            return is_inverse
        return True  # neutral favours nothing strongly


def _index_score(tech: dict[str, Any]) -> float | None:
    """Map one index proxy's technicals to a [-1, +1] sub-score."""
    price = float(tech.get("price") or 0.0)
    if price <= 0:
        return None
    sma50 = float(tech.get("sma_50") or 0.0)
    sma200 = float(tech.get("sma_200") or 0.0)
    trend = str(tech.get("trend_direction") or "mixed")

    score = 0.0
    if trend == "uptrend":
        score += 0.5
    elif trend == "downtrend":
        score -= 0.5

    if sma50 > 0 and sma200 > 0:
        score += 0.2 if price > sma50 else -0.2
        score += 0.3 if sma50 > sma200 else -0.3  # golden/death-cross alignment
    elif sma50 > 0:
        score += 0.25 if price > sma50 else -0.25

    return _clamp(score, -1.0, 1.0)


def _vix_state(vix: float | None) -> tuple[str, float]:
    """Classify VIX and return (state, score_adjustment)."""
    if vix is None or vix <= 0:
        return "unknown", 0.0
    if vix < 16:
        return "calm", 0.15
    if vix < 22:
        return "normal", 0.0
    if vix < _VIX_STRESS:
        return "elevated", -0.20
    return "stressed", -0.45


def _label_for(regime: str) -> str:
    return {"risk_on": "Risk-on", "risk_off": "Risk-off", "neutral": "Neutral"}.get(
        regime, "Neutral"
    )


def compute_regime(force: bool = False) -> RegimeState:
    """Compute (and briefly cache) the current market regime.

    Never raises and never fabricates: if market data is unavailable the result
    degrades to `neutral` with `stale=True` and a reason in the rationale.
    """
    global _cache
    now = time.time()
    with _cache_lock:
        if not force and _cache and (now - _cache[0]) < _CACHE_TTL_SECONDS:
            return _cache[1]

    components: dict[str, Any] = {}
    index_scores: list[float] = []
    errors: list[str] = []

    for sym in _INDEX_SYMBOLS:
        try:
            tech = get_technicals(sym, period="1y")
            sub = _index_score(tech)
            components[sym] = {
                "score": sub,
                "trend": tech.get("trend_direction"),
                "price": tech.get("price"),
                "sma50": tech.get("sma_50"),
                "sma200": tech.get("sma_200"),
            }
            if sub is not None:
                index_scores.append(sub)
        except LeveragedMarketError as exc:
            components[sym] = {"error": str(exc)}
            errors.append(f"{sym}: {exc}")
        except Exception as exc:  # noqa: BLE001 — regime must never crash a caller
            components[sym] = {"error": str(exc)}
            errors.append(f"{sym}: {exc}")

    vix: float | None = None
    try:
        vix = float(get_price(_VIX_SYMBOL).get("price") or 0.0) or None
    except Exception as exc:  # noqa: BLE001
        errors.append(f"VIX: {exc}")
    vix_state, vix_adj = _vix_state(vix)
    components["vix"] = {"level": vix, "state": vix_state}

    breadth: float | None = None
    if index_scores:
        breadth = sum(1 for s in index_scores if s > 0) / len(index_scores)

    base = (sum(index_scores) / len(index_scores)) if index_scores else 0.0
    score = _clamp(base + vix_adj, -1.0, 1.0)

    # Classify. A stressed VIX caps the read defensively: never risk-on under
    # stress, and risk-off only when the trend is genuinely negative (same
    # threshold, so no discontinuity at score==0 — a high-VIX melt-up reads
    # neutral, not a counter-trend inverse tilt).
    if vix is not None and vix >= _VIX_STRESS:
        regime = "risk_off" if score <= _RISK_OFF_AT else "neutral"
    elif score >= _RISK_ON_AT:
        regime = "risk_on"
    elif score <= _RISK_OFF_AT:
        regime = "risk_off"
    else:
        regime = "neutral"

    # Direction biases from the (signed) score, scaled into [0, 1].
    long_bias = _clamp(0.5 + score / 2.0, 0.0, 1.0)
    inverse_bias = _clamp(0.5 - score / 2.0, 0.0, 1.0)

    stale = not index_scores  # no usable index data → degraded
    if stale:
        regime, score, long_bias, inverse_bias = "neutral", 0.0, 0.5, 0.5

    bits: list[str] = []
    for sym in _INDEX_SYMBOLS:
        c = components.get(sym, {})
        if c.get("trend"):
            bits.append(f"{sym} {c['trend']}")
    if vix is not None:
        bits.append(f"VIX {vix:.1f} ({vix_state})")
    rationale = (
        f"{_label_for(regime)} (score {score:+.2f}): " + ", ".join(bits)
        if bits
        else f"{_label_for(regime)} — degraded market data ({'; '.join(errors)})"
    )

    state = RegimeState(
        regime=regime,
        label=_label_for(regime),
        score=round(score, 3),
        long_bias=round(long_bias, 3),
        inverse_bias=round(inverse_bias, 3),
        vix=round(vix, 2) if vix is not None else None,
        vix_state=vix_state,
        breadth=round(breadth, 3) if breadth is not None else None,
        rationale=rationale,
        components=components,
        as_of=datetime.now(tz=timezone.utc).isoformat(),
        stale=stale,
    )

    # Stamp the cache at completion time (not pre-fetch) so TTL reflects when
    # the data was actually read.
    with _cache_lock:
        _cache = (time.time(), state)
    return state


def regime_string() -> str:
    """Back-compat: the legacy hyphenated label used in agent summaries."""
    mapping = {"risk_on": "risk-on", "risk_off": "risk-off", "neutral": "neutral"}
    return mapping.get(compute_regime().regime, "neutral")
