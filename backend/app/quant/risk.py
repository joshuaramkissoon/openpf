"""Portfolio and return-series risk metrics.  Pure computation, no I/O."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ._helpers import TRADING_DAYS


def annualized_volatility(returns: pd.Series) -> float:
    """Annualized volatility (population std * sqrt(252))."""
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=0) * np.sqrt(TRADING_DAYS))


def max_drawdown(equity_curve: pd.Series) -> float:
    """Maximum peak-to-trough drawdown (negative number)."""
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
    """Annualized Sharpe ratio."""
    if returns.empty or returns.std(ddof=0) == 0:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS
    excess = returns - daily_rf
    ratio = excess.mean() / returns.std(ddof=0)
    return float(ratio * np.sqrt(TRADING_DAYS))


def sortino_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
    """Annualized Sortino ratio (downside deviation only)."""
    if returns.empty:
        return 0.0
    daily_rf = risk_free_rate / TRADING_DAYS
    excess = returns - daily_rf
    downside = returns[returns < daily_rf] - daily_rf
    if downside.empty or downside.std(ddof=0) == 0:
        return 0.0
    downside_std = float(downside.std(ddof=0))
    return float((excess.mean() / downside_std) * np.sqrt(TRADING_DAYS))


def value_at_risk(returns: pd.Series, confidence: float = 0.95) -> float:
    """Historical Value at Risk at the given confidence level.

    Returns a negative number representing the loss threshold.
    """
    if returns.empty:
        return 0.0
    return float(np.percentile(returns.dropna(), (1 - confidence) * 100))
