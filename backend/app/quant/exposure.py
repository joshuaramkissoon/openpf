"""Beta, factor exposure and concentration.  Pure computation, no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from ._helpers import MIN_OBS, TRADING_DAYS, QuantDataError, require_obs


@dataclass(frozen=True)
class Regression:
    alpha_annual: float  # intercept × 252
    betas: dict[str, float]
    r_squared: float
    residual_vol: float  # annualised idiosyncratic volatility
    n_obs: int


def ols_exposure(asset: pd.Series, factors: pd.DataFrame | pd.Series, *, min_obs: int = MIN_OBS) -> Regression:
    """OLS of daily asset returns on one or more factor return series (with intercept).

    With a single market factor this is the CAPM beta (cov/var); with several
    (e.g. SPY, QQQ−SPY, IWM−SPY, TLT) the betas are partial exposures.
    """
    fx = factors.to_frame() if isinstance(factors, pd.Series) else factors
    data = pd.concat([asset.rename("__y__"), fx], axis=1, join="inner").dropna()
    require_obs(len(data), "factor regression", min_obs=min_obs)
    y = data["__y__"].to_numpy()
    x = data.drop(columns="__y__").to_numpy()
    xd = np.column_stack([np.ones(len(x)), x])
    coef, *_ = np.linalg.lstsq(xd, y, rcond=None)
    resid = y - xd @ coef
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else 0.0
    dof = max(len(y) - xd.shape[1], 1)
    return Regression(
        alpha_annual=float(coef[0] * TRADING_DAYS),
        betas={str(c): float(b) for c, b in zip(fx.columns, coef[1:])},
        r_squared=r2,
        residual_vol=float(np.sqrt((resid**2).sum() / dof * TRADING_DAYS)),
        n_obs=len(y),
    )


def beta(asset: pd.Series, market: pd.Series, *, min_obs: int = MIN_OBS) -> float:
    """CAPM beta: cov(asset, market) / var(market) on aligned dates."""
    return ols_exposure(asset, market.rename("market"), min_obs=min_obs).betas["market"]


@dataclass(frozen=True)
class Concentration:
    hhi: float  # sum of squared weights (of invested weights normalised to 1)
    effective_n: float  # 1 / HHI — "you hold N names but it behaves like this many equal ones"
    top1: float
    top3: float
    top5: float
    n: int


def concentration(weights: Iterable[float]) -> Concentration:
    """Concentration of a long-only set of weights (normalised to sum to 1)."""
    w = np.array([max(float(x), 0.0) for x in weights])
    if w.sum() <= 0:
        raise QuantDataError("no positive weights")
    w = np.sort(w / w.sum())[::-1]
    hhi = float((w**2).sum())
    return Concentration(hhi, 1.0 / hhi, float(w[:1].sum()), float(w[:3].sum()), float(w[:5].sum()), int((w > 0).sum()))


def diversification_ratio(weights: np.ndarray, cov: np.ndarray) -> float:
    """Choueifaty & Coignard (2008): sum(w_i sigma_i) / sigma_p. 1 = no diversification benefit."""
    w = np.asarray(weights, dtype="float64")
    c = np.asarray(cov, dtype="float64")
    port = float(np.sqrt(max(w @ c @ w, 0.0)))
    if port == 0:
        raise QuantDataError("portfolio has zero variance")
    return float(w @ np.sqrt(np.diag(c)) / port)


def weighted_beta(betas: Mapping[str, float], weights: Mapping[str, float]) -> float:
    """Book beta = sum of weight × beta (cash has beta 0, so pass book weights)."""
    return float(sum(weights[k] * betas[k] for k in betas if k in weights))
