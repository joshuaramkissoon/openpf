"""Portfolio and return-series risk metrics.  Pure computation, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ._helpers import TRADING_DAYS, QuantDataError


def annualized_volatility(returns: pd.Series, *, ddof: int = 0) -> float:
    """Annualized volatility (std * sqrt(252); population std unless ``ddof=1``)."""
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=ddof) * np.sqrt(TRADING_DAYS))


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


# ── Tail risk, drawdowns and risk decomposition ─────────────────────────────
# (added for the quant toolkit; the functions above keep their old contracts)


def expected_shortfall(returns: pd.Series, confidence: float = 0.95) -> float:
    """Historical expected shortfall (CVaR): the mean return on the worst
    ``1 - confidence`` share of days. Negative number (a loss).

    Uses the "worst k days" definition with ``k = ceil(n * (1 - confidence))``
    so it is never better than the matching historical VaR and is coherent
    (Acerbi & Tasche 2002).
    """
    r = pd.Series(returns, dtype="float64").dropna().sort_values()
    if r.empty:
        raise QuantDataError("expected shortfall: no returns")
    k = max(1, int(np.ceil(len(r) * (1.0 - confidence) - 1e-9)))
    return float(r.iloc[:k].mean())


def tail_contributions(returns: pd.DataFrame, weights: dict[str, float], confidence: float = 0.95) -> dict[str, float]:
    """Each holding's share of the portfolio's historical expected shortfall.

    On the worst-k portfolio days, ES = mean(sum_i w_i r_i) = sum_i w_i mean(r_i),
    so the decomposition is exact: contributions sum to the portfolio ES.
    """
    cols = [c for c in returns.columns if c in weights]
    w = np.array([weights[c] for c in cols], dtype="float64")
    x = returns[cols].to_numpy()
    with np.errstate(all="ignore"):  # Accelerate (macOS) raises spurious FP warnings in matmul
        port = x @ w
    k = max(1, int(np.ceil(len(port) * (1.0 - confidence) - 1e-9)))
    worst = np.argsort(port, kind="stable")[:k]
    contrib = w * x[worst].mean(axis=0)
    return {c: float(v) for c, v in zip(cols, contrib)}


@dataclass(frozen=True)
class DrawdownStats:
    max_drawdown: float  # negative fraction, e.g. -0.32
    peak_date: object
    trough_date: object
    recovery_date: object | None  # None = not yet recovered
    max_duration_days: int  # longest underwater spell, in observations
    current_drawdown: float  # negative or 0
    calmar: float | None  # annualised return / |max drawdown|


def drawdown_stats(equity_curve: pd.Series, periods: int = TRADING_DAYS) -> DrawdownStats:
    """Max drawdown with its dates, longest underwater spell and current drawdown."""
    curve = pd.Series(equity_curve, dtype="float64").dropna()
    if len(curve) < 2:
        raise QuantDataError("drawdown: need at least 2 points")
    peak = curve.cummax()
    dd = curve / peak - 1.0
    trough = dd.idxmin()
    peak_date = curve.loc[:trough].idxmax()
    after = curve.loc[trough:]
    rec = after[after >= curve.loc[peak_date]]
    recovery = rec.index[0] if len(rec) and dd.min() < 0 else None
    longest = run = 0
    for underwater in (dd < 0).to_numpy():
        run = run + 1 if underwater else 0
        longest = max(longest, run)
    years = (len(curve) - 1) / periods
    total = float(curve.iloc[-1] / curve.iloc[0])
    ann = total ** (1 / years) - 1 if years > 0 and total > 0 else None
    mdd = float(dd.min())
    return DrawdownStats(
        max_drawdown=mdd,
        peak_date=peak_date,
        trough_date=trough,
        recovery_date=recovery,
        max_duration_days=int(longest),
        current_drawdown=float(dd.iloc[-1]),
        calmar=(ann / abs(mdd)) if (ann is not None and mdd < 0) else None,
    )


def ewma_volatility(returns: pd.Series, lam: float = 0.94, periods: int = TRADING_DAYS) -> float:
    """RiskMetrics EWMA volatility (annualised): s2_t = lam*s2_{t-1} + (1-lam)*r_{t-1}^2.

    Seeded with the mean square of the first 20 returns; reacts to regime
    changes much faster than an equal-weighted window. ``lam=0.94`` is the
    RiskMetrics (1996) daily value.
    """
    r = pd.Series(returns, dtype="float64").dropna().to_numpy()
    if len(r) < 21:
        raise QuantDataError("EWMA volatility: need at least 21 daily returns")
    var = float(np.mean(r[:20] ** 2))
    for x in r[20:]:
        var = lam * var + (1 - lam) * x * x
    return float(np.sqrt(var * periods))


def portfolio_volatility(weights: np.ndarray, cov: np.ndarray) -> float:
    """sqrt(w' S w) in the units of ``cov`` (annualise ``cov`` first for annual vol)."""
    w = np.asarray(weights, dtype="float64")
    return float(np.sqrt(max(float(w @ np.asarray(cov, dtype="float64") @ w), 0.0)))


@dataclass(frozen=True)
class RiskContributions:
    total_vol: float
    marginal: np.ndarray  # d sigma / d w_i = (S w)_i / sigma
    component: np.ndarray  # w_i * marginal_i  (sums to total_vol)
    pct: np.ndarray  # component / total_vol (sums to 1)


def risk_contributions(weights: np.ndarray, cov: np.ndarray) -> RiskContributions:
    """Euler decomposition of portfolio volatility (Litterman 1996; Qian 2006).

    Component contributions sum exactly to portfolio volatility because sigma
    is homogeneous of degree one in the weights.
    """
    w = np.asarray(weights, dtype="float64")
    c = np.asarray(cov, dtype="float64")
    sigma = portfolio_volatility(w, c)
    if sigma == 0:
        z = np.zeros_like(w)
        return RiskContributions(0.0, z, z, z)
    marginal = c @ w / sigma
    component = w * marginal
    return RiskContributions(sigma, marginal, component, component / sigma)
