"""Deploying cash: lump sum vs staged (DCA).  Pure computation, no I/O.

Evidence: with a positive equity risk premium, investing at once beats
averaging in on expectation; Vanguard's studies (2012, "Dollar-cost averaging
just means taking risk later"; refreshed 2023) found lump sum ahead of 12-month
DCA in roughly two thirds of rolling historical periods across the US, UK and
Australia, by ~2–2.4% on average. DCA's value is behavioural — it limits regret
from investing just before a fall — and matters most when the amount is large
relative to the existing book. This module quantifies that trade-off for the
specific amount, horizon and asset volatility instead of asserting it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DeployComparison:
    months: int
    expected_lump_gbp: float
    expected_staged_gbp: float
    prob_lump_wins: float
    lump_p05_gbp: float  # 5th percentile terminal value (bad case)
    staged_p05_gbp: float
    lump_worst_drop_gbp: float  # loss if the market fell `shock` right after a lump investment
    staged_worst_drop_gbp: float  # same shock, only the first tranche invested
    paths: int


def compare_lump_vs_staged(
    amount: float,
    months: int,
    *,
    expected_return: float = 0.07,
    vol: float = 0.18,
    cash_rate: float = 0.04,
    shock: float = 0.20,
    paths: int = 20_000,
    seed: int = 7,
) -> DeployComparison:
    """Monte Carlo (GBM, monthly steps, fixed seed) of lump sum vs ``months``
    equal monthly tranches, both measured at the end of the staging window.

    ``expected_return`` is the arithmetic annual return of the asset (a planning
    assumption, default ~7% for global equities); uninvested cash earns
    ``cash_rate``. Deterministic for a given seed.
    """
    if months < 1:
        raise ValueError("months must be >= 1")
    if months == 1:
        e = amount * float(np.exp(expected_return / 12))
        return DeployComparison(1, e, e, 0.5, e, e, amount * shock, amount * shock, 0)
    dt = 1.0 / 12
    rng = np.random.default_rng(seed)
    drift = (expected_return - 0.5 * vol * vol) * dt
    z = rng.standard_normal((paths, months))
    log_steps = drift + vol * np.sqrt(dt) * z  # month k return applies over (k, k+1]
    growth_to_end = np.exp(np.cumsum(log_steps[:, ::-1], axis=1)[:, ::-1])  # growth from month k to the end
    lump = amount * growth_to_end[:, 0]
    tranche = amount / months
    cash_growth = np.exp(cash_rate * dt * np.arange(months))  # cash waiting k months before tranche k
    staged = (tranche * cash_growth[None, :] * growth_to_end).sum(axis=1)
    exp_lump = amount * float(np.exp(expected_return * months * dt))
    exp_staged = float(sum(tranche * np.exp(cash_rate * k * dt) * np.exp(expected_return * (months - k) * dt) for k in range(months)))
    return DeployComparison(
        months=months,
        expected_lump_gbp=exp_lump,
        expected_staged_gbp=exp_staged,
        prob_lump_wins=float((lump > staged).mean()),
        lump_p05_gbp=float(np.percentile(lump, 5)),
        staged_p05_gbp=float(np.percentile(staged, 5)),
        lump_worst_drop_gbp=amount * shock,
        staged_worst_drop_gbp=tranche * shock,
        paths=paths,
    )


def staged_schedule(amount: float, tranches: int, spacing_weeks: int = 4) -> list[dict[str, float | int]]:
    """Equal tranches every ``spacing_weeks`` (pennies go to the last)."""
    if tranches < 1:
        raise ValueError("tranches must be >= 1")
    each = round(amount / tranches, 2)
    out = [{"tranche": i + 1, "week": i * spacing_weeks, "amount_gbp": each} for i in range(tranches)]
    out[-1]["amount_gbp"] = round(amount - each * (tranches - 1), 2)
    return out
