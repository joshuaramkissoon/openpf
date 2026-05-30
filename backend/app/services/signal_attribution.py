"""Signal-attribution loop.

Joins closed `LeveragedTrade` rows back to their originating `LeveragedSignal`
to answer: did our predicted edge/confidence actually materialise? The engine's
`expected_edge` and `confidence` were hand-tuned constants (review item #5) with
no feedback. This module measures realized-vs-predicted edge over a lookback
window and feeds a *data-driven* edge estimate back into signal generation.

No history → no change: with fewer than `_MIN_SAMPLES` closed trades the blend
returns the base estimate unchanged, so a fresh system behaves exactly as before
and only starts adapting once it has a real track record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import LeveragedSignal, LeveragedTrade

# Minimum closed trades (overall, and per-direction) before we trust the data.
_MIN_SAMPLES = 5
# How much weight the realized history gets vs the base constant when blending.
_BLEND_WEIGHT = 0.5
_EDGE_FLOOR, _EDGE_CEIL = 0.004, 0.03


def _regime_of(signal: LeveragedSignal) -> str | None:
    meta = signal.meta if isinstance(signal.meta, dict) else {}
    tech = meta.get("tech") if isinstance(meta.get("tech"), dict) else {}
    return tech.get("regime") or meta.get("regime")


def compute_attribution(db: Session, lookback_days: int = 120) -> dict[str, Any]:
    """Predicted-vs-realized edge over closed trades in the lookback window."""
    since = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(days=lookback_days)
    rows = list(
        db.execute(
            select(LeveragedTrade, LeveragedSignal)
            .join(LeveragedSignal, LeveragedTrade.signal_id == LeveragedSignal.id)
            .where(LeveragedTrade.status == "closed")
            .where(LeveragedTrade.signal_id.is_not(None))
        ).all()
    )
    # Filter by exit date in Python (exited_at may be naive; tolerate None).
    pairs: list[tuple[LeveragedTrade, LeveragedSignal]] = []
    for trade, signal in rows:
        if trade.exited_at is None or trade.exited_at >= since:
            pairs.append((trade, signal))

    def _bucket(items: list[tuple[LeveragedTrade, LeveragedSignal]]) -> dict[str, Any]:
        n = len(items)
        if n == 0:
            return {"n": 0, "win_rate": None, "avg_predicted_edge": None,
                    "avg_realized_pnl_pct": None, "avg_confidence": None, "edge_capture": None}
        wins = sum(1 for t, _ in items if (t.pnl_pct or 0.0) > 0)
        avg_pred = sum((s.expected_edge or 0.0) for _, s in items) / n
        avg_real = sum((t.pnl_pct or 0.0) for t, _ in items) / n
        avg_conf = sum((s.confidence or 0.0) for _, s in items) / n
        # Only compute the capture ratio when the predicted edge is meaningfully
        # non-zero — a near-zero divisor yields a spurious, unstable ratio.
        capture = (avg_real / avg_pred) if abs(avg_pred) > 1e-3 else None
        return {
            "n": n,
            "win_rate": round(wins / n, 3),
            "avg_predicted_edge": round(avg_pred, 5),
            "avg_realized_pnl_pct": round(avg_real, 5),
            "avg_confidence": round(avg_conf, 3),
            "edge_capture": round(capture, 3) if capture is not None else None,
        }

    by_direction = {
        "long": _bucket([p for p in pairs if p[1].direction == "long"]),
        "short": _bucket([p for p in pairs if p[1].direction in ("short", "inverse")]),
    }
    by_regime: dict[str, Any] = {}
    for trade, signal in pairs:
        reg = _regime_of(signal) or "unknown"
        by_regime.setdefault(reg, []).append((trade, signal))
    by_regime = {k: _bucket(v) for k, v in by_regime.items()}

    overall = _bucket(pairs)
    overall["lookback_days"] = lookback_days
    overall["calibrated"] = overall["n"] >= _MIN_SAMPLES
    return {
        "overall": overall,
        "by_direction": by_direction,
        "by_regime": by_regime,
    }


def data_driven_edge(attribution: dict[str, Any], direction: str, base_edge: float) -> float:
    """Blend the base (rule) edge with realized history for this direction.

    Falls back to `base_edge` unchanged when there isn't enough history, so the
    estimate only adapts once a real track record exists.
    """
    key = "short" if direction in ("short", "inverse") else "long"
    bucket = (attribution or {}).get("by_direction", {}).get(key, {})
    n = bucket.get("n") or 0
    realized = bucket.get("avg_realized_pnl_pct")
    if n < _MIN_SAMPLES or realized is None:
        return base_edge
    blended = (1 - _BLEND_WEIGHT) * base_edge + _BLEND_WEIGHT * float(realized)
    return max(_EDGE_FLOOR, min(_EDGE_CEIL, blended))
