"""Shared helpers for the quant package."""

from __future__ import annotations

import math

TRADING_DAYS = 252

# Minimum overlapping daily observations before a covariance / volatility
# estimate is trusted. ~3 months of trading days: below this the sampling error
# on a vol estimate is >±9% relative (1/sqrt(2n)) and correlations are noise.
MIN_OBS = 60


class QuantDataError(ValueError):
    """Raised when inputs are too short, misaligned or degenerate to compute a
    trustworthy answer. The message is written for a human (and for Archie to
    quote) — say what is missing and what would fix it."""


def require_obs(n: int, what: str, min_obs: int = MIN_OBS) -> None:
    if n < min_obs:
        raise QuantDataError(
            f"{what}: only {n} overlapping daily observations, need at least {min_obs} "
            f"(~{math.ceil(min_obs / 21)} months). Use a longer lookback or drop the newest listing."
        )


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
