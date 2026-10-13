"""Position sizing and allocation weights.  Pure computation, no I/O.

All weights are fractions of the *book* (total equity incl. cash) unless a
function says otherwise. Every method here answers "how big?" from risk, not
from a return forecast — except Kelly, which needs one and therefore takes it
as an explicit assumption and is always fractional and capped.

References
* Volatility targeting: Moreira & Muir (2017), "Volatility-Managed Portfolios", JF;
  Harvey et al. (2018), "The Impact of Volatility Targeting", JPM.
* Inverse-volatility / naive risk parity and ERC: Maillard, Roncalli & Teiletche
  (2010), "The Properties of Equally Weighted Risk Contribution Portfolios", JPM.
  Solver: cyclical coordinate descent, Griveau-Billion, Richard & Roncalli (2013).
* Kelly: Kelly (1956); continuous-time f* = (mu - r) / sigma^2 (Merton 1969;
  Thorp 2006). Fractional Kelly trades a little growth for a lot less
  drawdown: half-Kelly keeps ~75% of the growth rate at half the volatility
  (MacLean, Thorp & Ziemba 2010).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

from ._helpers import QuantDataError


def vol_scaled_weight(
    base_weight: float,
    asset_vol: float | None,
    reference_vol: float,
    bounds: tuple[float, float] = (0.5, 1.25),
) -> tuple[float, float]:
    """Scale a base weight by ``reference_vol / asset_vol`` (clamped to ``bounds``).

    The conviction table is calibrated for an asset at ``reference_vol``; a
    basket twice as volatile gets half the weight (within bounds) so its risk,
    not its notional, matches the conviction. Missing vol → factor 1.
    Returns ``(weight, factor)``.
    """
    if not asset_vol or asset_vol <= 0:
        return base_weight, 1.0
    lo, hi = bounds
    factor = max(lo, min(hi, reference_vol / asset_vol))
    return base_weight * factor, factor


def vol_target_weight(asset_vol: float, target_risk: float, cap: float | None = None) -> float:
    """Weight whose standalone annualised volatility contribution equals ``target_risk``.

    ``weight * asset_vol = target_risk`` — e.g. a 1.5% risk budget on a 45%-vol
    stock is a 3.3% position. Clipped to ``cap``.
    """
    if asset_vol <= 0:
        raise QuantDataError("volatility must be positive to vol-target a position")
    w = target_risk / asset_vol
    return min(w, cap) if cap is not None else w


def inverse_vol_weights(vols: Mapping[str, float]) -> dict[str, float]:
    """Weights proportional to 1/sigma, summing to 1 ("naive risk parity").

    Equals the ERC portfolio when all pairwise correlations are equal.
    """
    clean = {k: float(v) for k, v in vols.items() if v and v > 0}
    if not clean:
        raise QuantDataError("no positive volatilities to weight")
    inv = {k: 1.0 / v for k, v in clean.items()}
    s = sum(inv.values())
    return {k: v / s for k, v in inv.items()}


def risk_parity_weights(
    cov: pd.DataFrame,
    budgets: Mapping[str, float] | None = None,
    *,
    tol: float = 1e-10,
    max_iter: int = 10_000,
) -> dict[str, float]:
    """Equal (or budgeted) risk contribution weights, long-only, summing to 1.

    Solves min 1/2 x'Sx - sum b_i ln x_i by cyclical coordinate descent, then
    normalises; at the optimum each asset's share of portfolio variance equals
    its budget b_i.
    """
    names = list(cov.index)
    s = cov.to_numpy(dtype="float64")
    n = len(names)
    if n == 0:
        raise QuantDataError("empty covariance matrix")
    if np.any(np.diag(s) <= 0):
        raise QuantDataError("risk parity needs positive variances for every asset")
    b = np.array([float((budgets or {}).get(k, 1.0)) for k in names])
    if np.any(b <= 0):
        raise QuantDataError("risk budgets must be positive")
    b = b / b.sum()
    x = 1.0 / np.sqrt(np.diag(s))
    x = x / x.sum()
    for _ in range(max_iter):
        prev = x.copy()
        for i in range(n):
            c = float(s[i] @ x - s[i, i] * x[i])
            a = s[i, i]
            x[i] = (-c + np.sqrt(c * c + 4.0 * a * b[i])) / (2.0 * a)
        if np.max(np.abs(x - prev)) < tol:
            break
    w = x / x.sum()
    return {k: float(v) for k, v in zip(names, w)}


# Assumed Sharpe ratio per conviction level, used only when Kelly sizing has no
# explicit expected return. Deliberately modest: the US equity market's long-run
# Sharpe is ~0.3–0.4 and a concentrated theme basket should not be assumed to
# beat it by much. Treat as a planning assumption, never a forecast.
CONVICTION_SHARPE = {"low": 0.15, "medium": 0.3, "high": 0.45}


@dataclass(frozen=True)
class KellyResult:
    full_kelly: float  # f* = excess return / variance
    fraction: float  # the Kelly multiple applied (e.g. 0.5)
    fractional: float  # fraction * full_kelly
    weight: float  # after the cap (and floor at 0)
    cap: float | None
    cap_binding: bool
    expected_excess_return: float
    vol: float


def kelly_weight(
    expected_excess_return: float,
    vol: float,
    *,
    fraction: float = 0.5,
    cap: float | None = None,
) -> KellyResult:
    """Fractional, capped continuous-time Kelly weight for one bet.

    ``f* = (mu - r) / sigma^2``. A negative edge gives weight 0 (no shorting).
    """
    if vol <= 0:
        raise QuantDataError("Kelly sizing needs a positive volatility")
    if not 0 < fraction <= 1:
        raise QuantDataError("Kelly fraction must be in (0, 1] — full Kelly is already the growth-optimal maximum")
    full = expected_excess_return / (vol * vol)
    frac = full * fraction
    w = max(frac, 0.0)
    binding = cap is not None and w > cap
    if binding:
        w = float(cap)  # type: ignore[arg-type]
    return KellyResult(full, fraction, frac, w, cap, bool(binding), expected_excess_return, vol)


def stress_loss(weight: float, stress_drawdown: float) -> float:
    """Loss (fraction of book) if the position falls by ``stress_drawdown``."""
    return weight * stress_drawdown


def max_weight_for_loss(max_loss: float, stress_drawdown: float) -> float:
    """Largest weight whose stress loss stays within ``max_loss`` of book."""
    if stress_drawdown <= 0:
        raise QuantDataError("stress drawdown must be positive")
    return max_loss / stress_drawdown
