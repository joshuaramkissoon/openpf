"""Threshold rebalancing with tolerance bands and a turnover budget.  Pure.

Evidence: threshold ("band") rebalancing beats calendar rebalancing on
turnover for the same tracking error; trading back only to the band edge cuts
turnover further at a small tracking cost (Masters 2003, "Rebalancing", JPM;
Vanguard, "Best practices for portfolio rebalancing", 2010/2022). The default
band is the "5/25 rule" (Swedroe): rebalance an asset when it drifts by more
than the smaller of 5 percentage points or 25% of its target weight — wide
enough for big allocations, tight enough for small ones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

TradeTo = Literal["target", "edge"]


def band_for(target: float, abs_band: float = 0.05, rel_band: float = 0.25) -> float:
    """Tolerance band (in weight) around a target: min(abs_band, rel_band × target)."""
    return min(abs_band, rel_band * target) if target > 0 else 0.0


@dataclass
class RebalanceTrade:
    ticker: str
    current_weight: float
    target_weight: float
    band: float
    drift: float  # current - target
    notional_gbp: float  # + buy, - sell
    note: str = ""


@dataclass
class RebalancePlan:
    trades: list[RebalanceTrade]
    turnover_gbp: float  # sum of |trades|
    turnover_budget_gbp: float | None
    budget_binding: bool
    cash_binding: bool
    after_weights: dict[str, float]
    in_band: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)  # out of band but cut by budget/cash/min size


def rebalance_plan(
    current: Mapping[str, float],
    target: Mapping[str, float],
    total_value: float,
    *,
    abs_band: float = 0.05,
    rel_band: float = 0.25,
    trade_to: TradeTo = "target",
    turnover_budget: float | None = 0.20,
    cash_available_gbp: float = 0.0,
    min_trade_gbp: float = 50.0,
) -> RebalancePlan:
    """Trades (GBP) that bring out-of-band weights back to target (or band edge).

    * ``current`` / ``target``: weights of the book; names missing from
      ``target`` have target 0 (exit), names missing from ``current`` are buys.
    * ``turnover_budget``: max total traded value (buys + sells) as a fraction of
      ``total_value``; the largest drifts are fixed first, the last partially.
    * Buys are limited to sale proceeds plus ``cash_available_gbp``.
    """
    names = sorted(set(current) | set(target))
    rows: list[RebalanceTrade] = []
    in_band: list[str] = []
    for t in names:
        cur, tgt = float(current.get(t, 0.0)), float(target.get(t, 0.0))
        band = band_for(tgt, abs_band, rel_band)
        drift = cur - tgt
        if abs(drift) <= band + 1e-12:
            in_band.append(t)
            continue
        move = abs(drift) if trade_to == "target" or tgt == 0 else abs(drift) - band
        sign = -1.0 if drift > 0 else 1.0
        rows.append(RebalanceTrade(t, cur, tgt, band, drift, sign * move * total_value))

    rows.sort(key=lambda r: abs(r.notional_gbp), reverse=True)
    budget = turnover_budget * total_value if turnover_budget else None
    binding = False
    if budget is not None:
        left = budget
        for r in rows:
            want = abs(r.notional_gbp)
            take = min(want, max(left, 0.0))
            if take < want - 1e-9:
                binding = True
                r.note = "partly — turnover budget" if take > 0 else "deferred — turnover budget"
            r.notional_gbp = take if r.notional_gbp > 0 else -take
            left -= take

    sells = -sum(r.notional_gbp for r in rows if r.notional_gbp < 0)
    buys = sum(r.notional_gbp for r in rows if r.notional_gbp > 0)
    cash_binding = False
    funding = sells + max(cash_available_gbp, 0.0)
    if buys > funding + 1e-9 and buys > 0:
        cash_binding = True
        scale = funding / buys
        for r in rows:
            if r.notional_gbp > 0:
                r.notional_gbp *= scale
                r.note = (r.note + "; " if r.note else "") + "scaled to available cash"

    trades = [r for r in rows if abs(r.notional_gbp) >= min_trade_gbp]
    deferred = sorted(r.ticker for r in rows if abs(r.notional_gbp) < min_trade_gbp)
    after = {t: float(current.get(t, 0.0)) for t in names}
    for r in trades:
        after[r.ticker] = after.get(r.ticker, 0.0) + r.notional_gbp / total_value
    turnover = sum(abs(r.notional_gbp) for r in trades)
    return RebalancePlan(trades, turnover, budget, binding, cash_binding, after, sorted(in_band), deferred)
