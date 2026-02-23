"""Shared helpers for the quant package."""

from __future__ import annotations

import math

TRADING_DAYS = 252


def _safe_float(value: object) -> float | None:
    """Convert *value* to a Python float, returning ``None`` for nan / inf / bad types."""
    if value is None:
        return None
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f
