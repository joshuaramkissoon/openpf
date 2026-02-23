"""Helpers for converting pandas index + values into lightweight-charts point dicts."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ._helpers import _safe_float


def _format_time(ts: Any, intraday: bool) -> str | int:
    """Return a date string (``YYYY-MM-DD``) for daily data, or a Unix
    timestamp (int seconds) for intraday data."""
    if intraday:
        if isinstance(ts, pd.Timestamp):
            return int(ts.timestamp())
        return int(pd.Timestamp(ts).timestamp())
    # Daily
    if isinstance(ts, pd.Timestamp):
        return ts.strftime("%Y-%m-%d")
    return str(pd.Timestamp(ts).strftime("%Y-%m-%d"))


def indicator_to_points(
    index: pd.Index,
    values: pd.Series,
    intraday: bool = False,
) -> list[dict]:
    """Convert an index + values pair to ``[{time, value}, ...]``."""
    points: list[dict] = []
    for ts, val in zip(index, values):
        safe = _safe_float(val)
        if safe is None:
            continue
        points.append({"time": _format_time(ts, intraday), "value": safe})
    return points


def macd_to_points(
    index: pd.Index,
    macd: pd.Series,
    signal: pd.Series,
    hist: pd.Series,
    intraday: bool = False,
) -> list[dict]:
    """Convert MACD components to ``[{time, macd, signal, histogram}, ...]``."""
    points: list[dict] = []
    for ts, m, s, h in zip(index, macd, signal, hist):
        m_safe = _safe_float(m)
        s_safe = _safe_float(s)
        h_safe = _safe_float(h)
        if m_safe is None or s_safe is None or h_safe is None:
            continue
        points.append({
            "time": _format_time(ts, intraday),
            "macd": m_safe,
            "signal": s_safe,
            "histogram": h_safe,
        })
    return points
