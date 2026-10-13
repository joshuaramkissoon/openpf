"""UK tax-wrapper arithmetic for a Stocks ISA + General Investment Account.

Pure functions; the numbers are planning estimates, not tax advice. Rates are
for tax year 2026/27 and are parameters everywhere, so a change in the rules
is a one-line constant change.

Placement principle (asset location): the ISA allowance is scarce and
use-it-or-lose-it, so it should hold the assets with the largest expected
*taxable* return — high-growth/high-volatility holdings (largest potential
gains), high-yield holdings (dividends), and anything traded often. Losses
inside an ISA can't be offset, so a GIA is the better home for a speculative
position you may want to harvest a loss on. Bed & ISA (sell in the GIA, rebuy
in the ISA) moves holdings in, realising gains — ideally within the annual
CGT exemption. Leveraged/inverse ETPs on Trading 212 are ISA-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

ISA_ALLOWANCE_GBP = 20_000.0
CGT_ANNUAL_EXEMPT_GBP = 3_000.0
# Shares, from 30 Oct 2024.
CGT_RATE = {"basic": 0.18, "higher": 0.24, "additional": 0.24}
DIVIDEND_ALLOWANCE_GBP = 500.0
# From 6 April 2026 (Autumn Budget 2025: ordinary and upper rates +2pp).
DIVIDEND_RATE = {"basic": 0.1075, "higher": 0.3575, "additional": 0.3935}

Band = Literal["basic", "higher", "additional"]


def tax_year(d: date) -> tuple[date, date]:
    """(6 April start, 5 April end) of the UK tax year containing ``d``."""
    start_year = d.year if (d.month, d.day) >= (4, 6) else d.year - 1
    return date(start_year, 4, 6), date(start_year + 1, 4, 5)


def tax_year_label(d: date) -> str:
    s, e = tax_year(d)
    return f"{s.year}/{str(e.year)[-2:]}"


def days_to_year_end(d: date) -> int:
    return (tax_year(d)[1] - d).days


def isa_headroom(subscribed_gbp: float, allowance: float = ISA_ALLOWANCE_GBP) -> float:
    """Remaining ISA subscription allowance this tax year (never negative)."""
    return max(allowance - max(subscribed_gbp, 0.0), 0.0)


def cgt_due(gain_gbp: float, *, other_gains_gbp: float = 0.0, band: Band = "higher",
            exempt: float = CGT_ANNUAL_EXEMPT_GBP) -> float:
    """CGT on realising ``gain_gbp`` given gains already realised this year.

    Losses (negative gains) reduce the year's net gains. Uses a single rate
    for the band — a basic-rate taxpayer whose gains cross into the higher
    band pays 24% on the excess, which this ignores (conservative for higher
    rate, slightly low for basic-rate payers near the threshold).
    """
    before = max(other_gains_gbp - exempt, 0.0)
    after = max(other_gains_gbp + gain_gbp - exempt, 0.0)
    return max(after - before, 0.0) * CGT_RATE[band]


def tax_free_sale_gbp(value_gbp: float, gain_gbp: float, *, other_gains_gbp: float = 0.0,
                      exempt: float = CGT_ANNUAL_EXEMPT_GBP) -> float:
    """How much of a holding can be sold this tax year without CGT.

    Assumes the gain is spread evenly across the holding (Section 104 pooling
    gives every share the same average cost, so it is).
    """
    if value_gbp <= 0:
        return 0.0
    if gain_gbp <= 0:
        return value_gbp  # a loss (or flat) — selling creates no tax
    room = max(exempt - max(other_gains_gbp, 0.0), 0.0)
    return min(value_gbp, room / (gain_gbp / value_gbp))


@dataclass
class PlacementItem:
    ticker: str
    amount_gbp: float
    account: str  # "stocks_isa" | "invest" | "split"
    isa_gbp: float
    invest_gbp: float
    annual_tax_drag_gbp: float  # estimated yearly tax if held in the GIA
    reason: str


@dataclass
class PlacementResult:
    items: list[PlacementItem]
    isa_capacity_gbp: float
    isa_cash_gbp: float
    allowance_left_gbp: float
    needs_isa_deposit_gbp: float  # ISA placements beyond the ISA's current cash
    notes: list[str] = field(default_factory=list)


def gia_tax_drag(amount_gbp: float, *, dividend_yield: float, expected_growth: float,
                 band: Band = "higher", realisation_rate: float = 0.25) -> float:
    """Rough yearly tax a GIA holding would incur: dividends at the dividend rate
    plus the share of growth realised each year (``realisation_rate``) at the CGT
    rate. Allowances are ignored here (they are per person, not per holding);
    the score is for *ranking* what deserves the ISA, not for a tax bill."""
    div = amount_gbp * max(dividend_yield, 0.0) * DIVIDEND_RATE[band]
    cg = amount_gbp * max(expected_growth, 0.0) * realisation_rate * CGT_RATE[band]
    return div + cg


def place_buys(
    buys: list[dict],
    *,
    isa_cash_gbp: float,
    allowance_left_gbp: float,
    band: Band = "higher",
) -> PlacementResult:
    """Choose ISA vs Invest for each buy.

    ``buys`` items: ``ticker``, ``amount_gbp``, ``dividend_yield`` (fraction),
    ``expected_growth`` (fraction/yr, e.g. a vol-based proxy), optional
    ``isa_only`` (leveraged/inverse ETPs) and ``speculative`` (likely loss
    harvest → prefer GIA). ISA capacity = ISA free cash + remaining allowance
    (the latter needs the user to deposit first). Highest tax drag gets the ISA.
    """
    capacity = max(isa_cash_gbp, 0.0) + max(allowance_left_gbp, 0.0)
    scored = []
    for b in buys:
        drag = gia_tax_drag(float(b["amount_gbp"]), dividend_yield=float(b.get("dividend_yield") or 0.0),
                            expected_growth=float(b.get("expected_growth") or 0.0), band=band)
        rank = (0 if b.get("isa_only") else 1, 1 if b.get("speculative") else 0, -drag / max(float(b["amount_gbp"]), 1e-9))
        scored.append((rank, drag, b))
    scored.sort(key=lambda x: x[0])
    left = capacity
    items: list[PlacementItem] = []
    notes: list[str] = []
    for _rank, drag, b in scored:
        amt = float(b["amount_gbp"])
        t = str(b["ticker"])
        if b.get("speculative") and not b.get("isa_only"):
            items.append(PlacementItem(t, amt, "invest", 0.0, amt, drag,
                                       "speculative — keep in Invest so a loss can offset gains"))
            continue
        to_isa = min(amt, max(left, 0.0))
        left -= to_isa
        if b.get("isa_only") and to_isa < amt:
            notes.append(f"{t} is ISA-only on Trading 212 but only £{to_isa:,.0f} of ISA capacity is left.")
        if to_isa >= amt - 0.005:
            why = "ISA-only product" if b.get("isa_only") else f"highest tax drag (~£{drag:,.0f}/yr if held in Invest)"
            items.append(PlacementItem(t, amt, "stocks_isa", amt, 0.0, drag, why))
        elif to_isa > 0:
            items.append(PlacementItem(t, amt, "split", to_isa, amt - to_isa, drag,
                                       f"ISA capacity runs out — £{to_isa:,.0f} ISA, rest Invest"))
        else:
            items.append(PlacementItem(t, amt, "invest", 0.0, amt, drag, "no ISA capacity left this tax year"))
    isa_total = sum(i.isa_gbp for i in items)
    needs = max(isa_total - max(isa_cash_gbp, 0.0), 0.0)
    if needs > 0:
        notes.append(f"£{needs:,.0f} of the ISA placements needs a deposit into the ISA first (within the allowance).")
    return PlacementResult(items, capacity, isa_cash_gbp, allowance_left_gbp, needs, notes)
