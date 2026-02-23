"""Portfolio-level analytics.  Pure computation, no I/O."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def concentration_hhi(positions: Iterable[dict]) -> float:
    """Herfindahl-Hirschman Index: sum of squared weights."""
    weights = [max(float(p.get("weight", 0.0)), 0.0) for p in positions]
    return float(sum(w ** 2 for w in weights))


def portfolio_beta(
    asset_returns: list[pd.Series],
    asset_weights: list[float],
    benchmark_returns: pd.Series,
) -> float:
    """Weighted portfolio beta relative to a benchmark.

    Each element of *asset_returns* is aligned with *benchmark_returns* before
    computing the individual beta (covariance / variance).  Returns 0.0 when
    the benchmark has zero variance.
    """
    if benchmark_returns.empty or benchmark_returns.var(ddof=0) == 0:
        return 0.0

    bench_var = float(benchmark_returns.var(ddof=0))
    weighted_beta = 0.0
    total_weight = 0.0

    for ret, w in zip(asset_returns, asset_weights):
        # Align on common index
        aligned = pd.concat([ret, benchmark_returns], axis=1, join="inner").dropna()
        if aligned.empty:
            continue
        asset_col = aligned.iloc[:, 0]
        bench_col = aligned.iloc[:, 1]
        cov = float(asset_col.cov(bench_col))
        beta = cov / bench_var
        weighted_beta += beta * w
        total_weight += w

    if total_weight == 0:
        return 0.0
    return float(weighted_beta / total_weight)


def correlation_matrix(return_series: dict[str, pd.Series]) -> dict:
    """Correlation matrix from a dict of return series.

    Returns ``{"tickers": [...], "matrix": [[...], ...]}``.
    """
    if not return_series:
        return {"tickers": [], "matrix": []}

    tickers = list(return_series.keys())
    df = pd.DataFrame(return_series)
    corr = df.corr()

    return {
        "tickers": tickers,
        "matrix": [[float(corr.iloc[i, j]) for j in range(len(tickers))] for i in range(len(tickers))],
    }
