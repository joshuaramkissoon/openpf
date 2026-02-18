from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.market_data import fetch_history
from app.utils.indicators import max_drawdown, sharpe_ratio


@dataclass
class BacktestResult:
    symbol: str
    lookback_days: int
    fast_window: int
    slow_window: int
    trades: int
    cagr: float
    max_drawdown: float
    sharpe: float
    win_rate: float
    equity_curve: list[dict]


def run_ma_crossover_backtest(symbol: str, lookback_days: int, fast_window: int, slow_window: int) -> BacktestResult:
    if fast_window >= slow_window:
        raise ValueError("fast_window must be smaller than slow_window")

    history = fetch_history(symbol, lookback_days=max(lookback_days + slow_window + 30, 220))
    prices = history["close"]

    fast = prices.rolling(fast_window).mean()
    slow = prices.rolling(slow_window).mean()

    signal = (fast > slow).astype(int)
    position = signal.shift(1).fillna(0)

    returns = prices.pct_change().fillna(0)
    strategy_returns = returns * position

    equity = (1 + strategy_returns).cumprod()
    benchmark = (1 + returns).cumprod()

    # Restrict to requested lookback region
    slice_df = history.copy()
    slice_df["strategy"] = equity
    slice_df["benchmark"] = benchmark
    slice_df["position"] = position
    slice_df = slice_df.tail(lookback_days).reset_index(drop=True)

    strategy_ret = slice_df["strategy"].pct_change().fillna(0)
    years = len(slice_df) / 252
    ending = float(slice_df["strategy"].iloc[-1])
    cagr = ending ** (1 / years) - 1 if years > 0 else 0.0

    positions = slice_df["position"].fillna(0)
    trades = int((positions.diff().abs() > 0).sum())

    trade_pnls = []
    current_entry = None
    current_side = 0
    for _, row in slice_df.iterrows():
        side = int(row["position"])
        px = float(row["close"])
        if side == 1 and current_side == 0:
            current_entry = px
        if side == 0 and current_side == 1 and current_entry is not None:
            trade_pnls.append(px / current_entry - 1)
            current_entry = None
        current_side = side

    win_rate = float(np.mean([1 if x > 0 else 0 for x in trade_pnls])) if trade_pnls else 0.0

    curve = [
        {
            "date": row["date"],
            "strategy": float(row["strategy"]),
            "benchmark": float(row["benchmark"]),
        }
        for _, row in slice_df.iterrows()
    ]

    return BacktestResult(
        symbol=symbol.upper(),
        lookback_days=lookback_days,
        fast_window=fast_window,
        slow_window=slow_window,
        trades=trades,
        cagr=float(cagr),
        max_drawdown=max_drawdown(slice_df["strategy"]),
        sharpe=sharpe_ratio(strategy_ret),
        win_rate=win_rate,
        equity_curve=curve,
    )
