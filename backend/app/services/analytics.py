from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from app.services.market_data import MarketDataError, fetch_history, normalize_symbol_for_yf
from app.utils.indicators import annualized_volatility, compute_rsi


@dataclass
class PositionSignal:
    ticker: str
    momentum_63d: float | None
    rsi_14: float | None
    trend_score: float | None
    volatility_30d: float | None
    risk_flag: str | None


def _score_trend(close: pd.Series) -> float:
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else close.rolling(100).mean().iloc[-1]
    latest = close.iloc[-1]

    score = 0.0
    if latest > sma20:
        score += 0.25
    if sma20 > sma50:
        score += 0.25
    if sma50 > sma200:
        score += 0.5
    return float(score)


def signal_for_symbol(symbol: str) -> PositionSignal:
    try:
        history = fetch_history(symbol, lookback_days=420)
    except MarketDataError:
        return PositionSignal(
            ticker=normalize_symbol_for_yf(symbol),
            momentum_63d=None,
            rsi_14=None,
            trend_score=None,
            volatility_30d=None,
            risk_flag="no-market-data",
        )

    close = history["close"]
    returns = close.pct_change().dropna()

    momentum_63d = None
    if len(close) > 64:
        momentum_63d = float(close.iloc[-1] / close.iloc[-64] - 1)

    rsi_series = compute_rsi(close, 14)
    rsi_14 = float(rsi_series.iloc[-1]) if not rsi_series.empty else None

    trend_score = _score_trend(close) if len(close) >= 50 else None
    vol_30 = annualized_volatility(returns.tail(30)) if not returns.empty else None

    risk_flag = None
    if rsi_14 is not None and rsi_14 > 75:
        risk_flag = "overbought"
    elif rsi_14 is not None and rsi_14 < 30:
        risk_flag = "oversold"

    return PositionSignal(
        ticker=normalize_symbol_for_yf(symbol),
        momentum_63d=momentum_63d,
        rsi_14=rsi_14,
        trend_score=trend_score,
        volatility_30d=vol_30,
        risk_flag=risk_flag,
    )


def estimate_portfolio_beta(positions: list[dict], max_assets: int = 8) -> float:
    if not positions:
        return 0.0

    try:
        benchmark = fetch_history("SPY", lookback_days=260)
    except MarketDataError:
        return 1.0

    bench = benchmark["close"].pct_change().dropna()
    if bench.empty:
        return 1.0

    weighted_beta = 0.0
    total_weight = 0.0

    candidates = sorted(positions, key=lambda x: float(x.get("weight", 0.0)), reverse=True)[:max_assets]

    for p in candidates:
        weight = max(float(p.get("weight", 0.0)), 0.0)
        if weight <= 0:
            continue
        symbol = p.get("instrument_code") or p.get("ticker")
        try:
            history = fetch_history(symbol, lookback_days=260)
        except MarketDataError:
            continue

        ret = history["close"].pct_change().dropna()
        n = min(len(ret), len(bench))
        if n < 40:
            continue
        asset = ret.tail(n).to_numpy()
        benchmark = bench.tail(n).to_numpy()
        cov = np.cov(asset, benchmark)[0, 1]
        var_b = np.var(benchmark)
        beta = cov / var_b if var_b else 1.0

        weighted_beta += beta * weight
        total_weight += weight

    if total_weight == 0:
        return 1.0
    return float(weighted_beta / total_weight)


def concentration_hhi(positions: Iterable[dict]) -> float:
    weights = [max(float(p.get("weight", 0.0)), 0.0) for p in positions]
    return float(sum(w**2 for w in weights))


def estimated_portfolio_volatility(positions: Iterable[dict]) -> float:
    vol = 0.0
    for p in positions:
        weight = max(float(p.get("weight", 0.0)), 0.0)
        vol += (float(p.get("volatility_30d") or 0.0) * weight)
    return float(vol)
