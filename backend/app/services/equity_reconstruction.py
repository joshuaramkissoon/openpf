"""Reconstruct a historical daily equity curve from order/cashflow history.

Pure: given normalized fills, cash events, and price/FX lookups, produce a daily
{date, holdings, cash, total} series in the target currency. All I/O (T212 +
yfinance + FX backfill) lives in the backfill service which feeds this engine.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable


@dataclass(frozen=True)
class Fill:
    on: date
    key: str            # instrument identity for grouping + price lookup
    qty: float          # signed shares (negative = sell), as executed
    cash_impact: float  # signed cash impact in `account_ccy` (buy < 0, sell > 0)
    account_ccy: str
    instrument_ccy: str
    price: float = 0.0  # as-executed unit price (instrument ccy) — ground truth
                        # used to sanity-check / fall back from bad market data


@dataclass(frozen=True)
class CashEvent:
    on: date
    amount: float       # signed, in `ccy` (deposit/dividend > 0, withdrawal < 0)
    ccy: str


def normalize_split_basis(
    fills: list[Fill],
    splits_by_key: dict[str, list[tuple[date, float]]],
) -> list[Fill]:
    """Scale each fill's quantity to today's split basis.

    yfinance prices are split-adjusted (auto_adjust), but T212 records fills in
    as-executed share counts. For market value (shares × adjusted price) to be
    correct on every date, a fill placed before a split must be expressed in
    post-split units: multiply by the product of every split ratio dated *after*
    the fill (forward 4:1 → ×4; reverse 1:50 → ×0.02). Cash impact is unchanged —
    a split moves shares, not money."""
    out: list[Fill] = []
    for f in fills:
        factor = 1.0
        for sdate, ratio in splits_by_key.get(f.key, ()):  # type: ignore[arg-type]
            if sdate > f.on and ratio and ratio > 0:
                factor *= ratio
        out.append(f if factor == 1.0 else Fill(
            on=f.on, key=f.key, qty=f.qty * factor, cash_impact=f.cash_impact,
            account_ccy=f.account_ccy, instrument_ccy=f.instrument_ccy,
        ))
    return out


def reconstruct_daily_equity(
    *,
    fills: list[Fill],
    cash_events: list[CashEvent],
    price_lookup: Callable[[str, date], float | None],
    fx_lookup: Callable[[str, str, date], float],
    current_qty: dict[str, float],
    current_cash_base: float,
    target_ccy: str,
    start: date,
    end: date,
) -> list[dict]:
    keys = {f.key for f in fills}

    # Per-instrument anchor (split / rename / residual correction): the sum of
    # as-executed fills, scaled to match what you actually hold today. For a
    # buy-and-hold through an N:1 split this factor *is* N, so split-adjusted
    # share counts line up with split-adjusted (yfinance) prices automatically.
    reconstructed_today: dict[str, float] = defaultdict(float)
    for f in fills:
        reconstructed_today[f.key] += f.qty
    adj: dict[str, float] = {}
    for k in keys:
        rt = reconstructed_today[k]
        cq = current_qty.get(k)
        adj[k] = (cq / rt) if (cq is not None and abs(rt) > 1e-9) else 1.0

    fills_by_key: dict[str, list[Fill]] = defaultdict(list)
    for f in fills:
        fills_by_key[f.key].append(f)
    for flist in fills_by_key.values():
        flist.sort(key=lambda x: x.on)
    instr_ccy_of = {k: fills_by_key[k][0].instrument_ccy for k in keys}

    # Signed cash deltas per day in target currency: external flows + dividends
    # (cash_events) plus each fill's cash impact, converted at the day's own FX.
    cash_deltas: dict[date, float] = defaultdict(float)
    for ce in cash_events:
        cash_deltas[ce.on] += ce.amount * fx_lookup(ce.ccy, target_ccy, ce.on)
    for f in fills:
        cash_deltas[f.on] += f.cash_impact * fx_lookup(f.account_ccy, target_ccy, f.on)

    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    raw_shares: dict[str, float] = defaultdict(float)
    ptr: dict[str, int] = defaultdict(int)
    cash_raw = 0.0
    raw_series: list[tuple[date, float, float]] = []
    for day in days:
        for k in keys:
            flist = fills_by_key[k]
            while ptr[k] < len(flist) and flist[ptr[k]].on <= day:
                raw_shares[k] += flist[ptr[k]].qty
                ptr[k] += 1
        cash_raw += cash_deltas.get(day, 0.0)

        holdings = 0.0
        for k in keys:
            shares = raw_shares[k] * adj[k]
            if abs(shares) < 1e-12:
                continue
            price = price_lookup(k, day)
            if price is None:
                continue
            holdings += shares * price * fx_lookup(instr_ccy_of[k], target_ccy, day)
        raw_series.append((day, holdings, cash_raw))

    # Additive cash anchor: shift the whole cash curve so the right edge equals
    # today's real free cash (corrects unmodelled fees / interest / FX charges).
    cash_offset = (current_cash_base - raw_series[-1][2]) if raw_series else 0.0

    return [
        {
            "date": day.isoformat(),
            "holdings": round(holdings, 2),
            "cash": round(cash_raw_d + cash_offset, 2),
            "total": round(holdings + cash_raw_d + cash_offset, 2),
        }
        for day, holdings, cash_raw_d in raw_series
    ]
