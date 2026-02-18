from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def annualized_volatility(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=0) * np.sqrt(TRADING_DAYS))


def max_drawdown(equity_curve: pd.Series) -> float:
    if equity_curve.empty:
        return 0.0
    running_max = equity_curve.cummax()
    drawdown = equity_curve / running_max - 1.0
    return float(drawdown.min())


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = 0.02) -> float:
    if returns.empty or returns.std(ddof=0) == 0:
        return 0.0

    daily_rf = risk_free_rate / TRADING_DAYS
    excess = returns - daily_rf
    ratio = excess.mean() / returns.std(ddof=0)
    return float(ratio * np.sqrt(TRADING_DAYS))
