"""Return construction and alignment.  Pure computation, no I/O."""

from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd

from ._helpers import MIN_OBS, QuantDataError, require_obs


def simple_returns(prices: pd.Series) -> pd.Series:
    """Daily simple returns from a price series (drops non-positive prices and the first NaN)."""
    p = pd.Series(prices, dtype="float64").dropna()
    p = p[p > 0]
    return p.pct_change().dropna()


def convert_returns(local: pd.Series, fx_returns: pd.Series) -> pd.Series:
    """Returns in the investor's currency: ``(1 + r_local)(1 + r_fx) - 1``.

    ``fx_returns`` is the return of *one unit of the asset's currency* measured in
    the investor's currency (e.g. for a GBP investor holding USD assets, the
    daily change in GBP per USD). Dates are inner-joined.
    """
    joined = pd.concat([local.rename("a"), fx_returns.rename("fx")], axis=1, join="inner").dropna()
    return ((1.0 + joined["a"]) * (1.0 + joined["fx"]) - 1.0).rename(local.name)


def align_returns(series: Mapping[str, pd.Series], *, min_obs: int = MIN_OBS) -> pd.DataFrame:
    """Inner-join several return series on date.

    Raises :class:`QuantDataError` when fewer than ``min_obs`` common dates
    remain, naming the shortest series so the caller can drop it.
    """
    if not series:
        raise QuantDataError("no return series supplied")
    frame = pd.concat({k: pd.Series(v, dtype="float64") for k, v in series.items()}, axis=1, join="inner").dropna()
    if len(frame) < min_obs:
        lengths = {k: int(pd.Series(v).dropna().shape[0]) for k, v in series.items()}
        shortest = min(lengths, key=lengths.get)  # type: ignore[arg-type]
        raise QuantDataError(
            f"only {len(frame)} overlapping days across {len(series)} series (need {min_obs}); "
            f"shortest history is {shortest} with {lengths[shortest]} days"
        )
    return frame


def portfolio_returns(returns: pd.DataFrame, weights: Mapping[str, float]) -> pd.Series:
    """Daily returns of a constant-weight (daily-rebalanced) portfolio.

    Weights are used as given (not renormalised) so a partly-invested book
    (weights summing below 1, the rest in cash at 0%) is represented honestly.
    """
    cols = [c for c in returns.columns if c in weights]
    if not cols:
        raise QuantDataError("none of the weighted tickers have return data")
    w = np.array([float(weights[c]) for c in cols])
    with np.errstate(all="ignore"):  # Accelerate (macOS) raises spurious FP warnings in matmul
        out = returns[cols].to_numpy() @ w
    if not np.all(np.isfinite(out)):
        raise QuantDataError("portfolio returns are not finite — check the price data")
    return pd.Series(out, index=returns.index, name="portfolio")


def equity_curve(returns: pd.Series, start: float = 1.0) -> pd.Series:
    """Compound a return series into a value path starting at ``start``."""
    return start * (1.0 + returns).cumprod()


def annualised_return(returns: pd.Series, periods: int = 252) -> float:
    """Geometric annualised return of a daily return series."""
    r = pd.Series(returns).dropna()
    require_obs(len(r), "annualised return", min_obs=2)
    growth = float((1.0 + r).prod())
    if growth <= 0:
        return -1.0
    return growth ** (periods / len(r)) - 1.0
