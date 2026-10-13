"""Covariance estimation with shrinkage.  Pure computation, no I/O.

Why shrinkage: with N assets and T days the sample covariance has N(N+1)/2
free parameters estimated from T·N numbers. For a 15-name book on a year of
data that is 120 parameters from ~3,800 numbers — enough for the diagonal, but
the off-diagonals are noisy and the matrix's smallest eigenvalues are biased
towards zero. Anything that *inverts* or *optimises over* the matrix (risk
parity, minimum variance, HRP's inverse-variance step) then loads up on
spurious "diversifiers". Ledoit & Wolf shrink the sample matrix towards a
structured target by the analytically optimal amount.

* ``ledoit_wolf`` (default) — constant-correlation target: every pair gets the
  average sample correlation, variances untouched. Ledoit & Wolf (2003/2004),
  "Honey, I Shrunk the Sample Covariance Matrix", J. Portfolio Management.
  This is the target they recommend for equities.
* ``lw_identity`` — scaled-identity target, Ledoit & Wolf (2004), "A
  well-conditioned estimator for large-dimensional covariance matrices", JMVA
  (what ``sklearn.covariance.LedoitWolf`` implements).
* ``sample`` — plain maximum-likelihood (1/T) covariance; right for measuring
  the realised variance of a *fixed* portfolio, where nothing is inverted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from ._helpers import MIN_OBS, TRADING_DAYS, QuantDataError, require_obs

CovMethod = Literal["ledoit_wolf", "lw_identity", "sample"]


@dataclass(frozen=True)
class CovEstimate:
    """A daily covariance estimate plus how it was made."""

    matrix: pd.DataFrame  # daily covariance
    method: str
    shrinkage: float | None  # weight on the target (0 = sample, 1 = target)
    n_obs: int

    def annualised(self, periods: int = TRADING_DAYS) -> pd.DataFrame:
        return self.matrix * periods

    def vols(self, periods: int = TRADING_DAYS) -> pd.Series:
        """Annualised volatilities from the diagonal."""
        return pd.Series(np.sqrt(np.diag(self.matrix.to_numpy()) * periods), index=self.matrix.index)

    def correlation(self) -> pd.DataFrame:
        return cov_to_corr(self.matrix)


def cov_to_corr(cov: pd.DataFrame) -> pd.DataFrame:
    sd = np.sqrt(np.diag(cov.to_numpy()))
    if np.any(sd <= 0):
        raise QuantDataError("a series has zero variance (flat price) — drop it before computing correlation")
    corr = cov.to_numpy() / np.outer(sd, sd)
    np.fill_diagonal(corr, 1.0)
    return pd.DataFrame(np.clip(corr, -1.0, 1.0), index=cov.index, columns=cov.columns)


def _centered(returns: pd.DataFrame) -> np.ndarray:
    x = returns.to_numpy(dtype="float64")
    return x - x.mean(axis=0, keepdims=True)


def sample_covariance(returns: pd.DataFrame) -> np.ndarray:
    """Maximum-likelihood (divide by T) covariance of the columns."""
    x = _centered(returns)
    return x.T @ x / x.shape[0]


def ledoit_wolf_identity(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2004) shrinkage towards ``mu * I``; returns (cov, shrinkage)."""
    x = _centered(returns)
    t, n = x.shape
    s = x.T @ x / t
    mu = np.trace(s) / n
    target = mu * np.eye(n)
    delta2 = float(np.sum((s - target) ** 2))
    # beta_bar^2 = 1/T^2 * sum_t ||x_t x_t' - S||_F^2
    x2 = x**2
    with np.errstate(all="ignore"):
        beta_bar2 = float(np.sum(x2.T @ x2) / t - np.sum(s**2)) / t
    beta2 = min(beta_bar2, delta2)
    shrink = 0.0 if delta2 == 0 else beta2 / delta2
    return shrink * target + (1.0 - shrink) * s, float(shrink)


def ledoit_wolf_constant_correlation(returns: pd.DataFrame) -> tuple[np.ndarray, float]:
    """Ledoit-Wolf (2003) shrinkage towards a constant-correlation matrix.

    Follows the authors' reference implementation (``covCor.m``). Variances are
    preserved exactly; only covariances move towards ``r_bar * s_i * s_j``.
    """
    x = _centered(returns)
    t, n = x.shape
    s = x.T @ x / t
    var = np.diag(s)
    if np.any(var <= 0):
        raise QuantDataError("a series has zero variance (flat price) — cannot shrink its covariance")
    sd = np.sqrt(var)
    corr = s / np.outer(sd, sd)
    r_bar = (corr.sum() - n) / (n * (n - 1)) if n > 1 else 0.0
    target = r_bar * np.outer(sd, sd)
    np.fill_diagonal(target, var)

    # (macOS Accelerate can raise spurious FP warnings on tiny products; the
    # result is checked for finiteness below instead.)
    with np.errstate(all="ignore"):
        y = x**2
        pi_mat = y.T @ y / t - s**2
        theta = (x**3).T @ x / t - var[:, None] * s  # theta[i, j] = E[(x_i^2 - s_ii)(x_i x_j - s_ij)]
    if not (np.all(np.isfinite(pi_mat)) and np.all(np.isfinite(theta))):
        raise QuantDataError("covariance shrinkage failed on non-finite returns — check the price data")
    pi_hat = float(pi_mat.sum())

    # rho: diagonal part + off-diagonal correction for estimating r_bar.
    rho_diag = float(np.trace(pi_mat))
    ratio = np.outer(1.0 / sd, sd)  # ratio[i, j] = sd_j / sd_i
    term = ratio * theta  # sqrt(s_jj/s_ii) * theta_ii,ij
    off = term + term.T
    np.fill_diagonal(off, 0.0)
    rho_hat = rho_diag + r_bar / 2.0 * float(off.sum())

    gamma_hat = float(np.sum((target - s) ** 2))
    if gamma_hat == 0:
        return s, 0.0
    kappa = (pi_hat - rho_hat) / gamma_hat
    shrink = float(max(0.0, min(1.0, kappa / t)))
    return shrink * target + (1.0 - shrink) * s, shrink


def covariance(
    returns: pd.DataFrame,
    method: CovMethod = "ledoit_wolf",
    *,
    min_obs: int = MIN_OBS,
) -> CovEstimate:
    """Daily covariance of aligned returns (columns = assets).

    Raises :class:`QuantDataError` below ``min_obs`` rows or with a flat series.
    """
    frame = returns.dropna()
    require_obs(len(frame), "covariance", min_obs=min_obs)
    cols = list(frame.columns)
    if len(cols) == 1 or method == "sample":
        mat, shrink = sample_covariance(frame), None
        method = "sample" if len(cols) > 1 else method
    elif method == "lw_identity":
        mat, shrink = ledoit_wolf_identity(frame)
    elif method == "ledoit_wolf":
        mat, shrink = ledoit_wolf_constant_correlation(frame)
    else:  # pragma: no cover — guarded by the Literal
        raise ValueError(f"unknown covariance method {method!r}")
    return CovEstimate(pd.DataFrame(mat, index=cols, columns=cols), method, shrink, len(frame))
