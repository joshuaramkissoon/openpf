"""Tests for the historical equity-curve reconstruction engine (pure)."""

from datetime import date
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.equity_reconstruction import (
    CashEvent,
    Fill,
    reconstruct_daily_equity,
)


def _const_price(price):
    return lambda key, on: price


def _flat_fx(key=None, on=None):  # USD==GBP for arithmetic simplicity
    return 1.0


def _series_to_map(series):
    return {p["date"]: p for p in series}


def test_single_buy_held_is_valued_at_shares_times_price_each_day():
    # Buy 10 @ $10 on day 0; price flat $10; anchor matches (no split).
    fills = [Fill(on=date(2024, 1, 1), key="AAA", qty=10.0, cash_impact=-100.0,
                  account_ccy="USD", instrument_ccy="USD")]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=[], price_lookup=_const_price(10.0),
        fx_lookup=lambda s, t, on: 1.0, current_qty={"AAA": 10.0},
        current_cash_base=0.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 3),
    )
    m = _series_to_map(series)
    assert len(series) == 3
    assert m["2024-01-01"]["holdings"] == 100.0
    assert m["2024-01-02"]["holdings"] == 100.0
    assert m["2024-01-03"]["holdings"] == 100.0


def test_holdings_converted_with_fx_on_each_date():
    # Instrument priced in USD, target GBP; fx 0.5 day1, 0.8 day2.
    fills = [Fill(on=date(2024, 1, 1), key="AAA", qty=2.0, cash_impact=-200.0,
                  account_ccy="USD", instrument_ccy="USD")]

    def fx(src, tgt, on):
        return {date(2024, 1, 1): 0.5, date(2024, 1, 2): 0.8}[on]

    series = reconstruct_daily_equity(
        fills=fills, cash_events=[], price_lookup=_const_price(100.0),
        fx_lookup=fx, current_qty={"AAA": 2.0}, current_cash_base=0.0,
        target_ccy="GBP", start=date(2024, 1, 1), end=date(2024, 1, 2),
    )
    m = _series_to_map(series)
    assert m["2024-01-01"]["holdings"] == 100.0   # 2 * 100 * 0.5
    assert m["2024-01-02"]["holdings"] == 160.0   # 2 * 100 * 0.8


def test_split_is_corrected_by_anchoring_to_current_quantity():
    # Bought 10 shares pre-split; held through a 10:1 split → hold 100 today.
    # yfinance prices are split-adjusted (flat $10), so valuing the
    # split-adjusted share count (100) must reproduce the real value ($1000).
    fills = [Fill(on=date(2024, 1, 1), key="NVDA", qty=10.0, cash_impact=-1000.0,
                  account_ccy="USD", instrument_ccy="USD")]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=[], price_lookup=_const_price(10.0),
        fx_lookup=lambda s, t, on: 1.0, current_qty={"NVDA": 100.0},
        current_cash_base=0.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 1),
    )
    assert series[0]["holdings"] == 1000.0  # 10 * 10 (split factor) * $10


def test_position_sold_to_zero_drops_out_of_holdings():
    fills = [
        Fill(on=date(2024, 1, 1), key="AAA", qty=10.0, cash_impact=-100.0,
             account_ccy="USD", instrument_ccy="USD"),
        Fill(on=date(2024, 1, 3), key="AAA", qty=-10.0, cash_impact=100.0,
             account_ccy="USD", instrument_ccy="USD"),
    ]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=[], price_lookup=_const_price(10.0),
        fx_lookup=lambda s, t, on: 1.0, current_qty={},  # nothing held today
        current_cash_base=0.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 4),
    )
    m = _series_to_map(series)
    assert m["2024-01-01"]["holdings"] == 100.0
    assert m["2024-01-02"]["holdings"] == 100.0
    assert m["2024-01-03"]["holdings"] == 0.0
    assert m["2024-01-04"]["holdings"] == 0.0


def test_cash_tracks_deposits_and_fill_impacts_then_anchors_to_today():
    # Deposit 1000 on day0, buy 100 worth on day0 → real cash 900 today.
    # current_cash_base = 900 → additive anchor offset is 0.
    fills = [Fill(on=date(2024, 1, 1), key="AAA", qty=10.0, cash_impact=-100.0,
                  account_ccy="USD", instrument_ccy="USD")]
    cash = [CashEvent(on=date(2024, 1, 1), amount=1000.0, ccy="USD")]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=cash, price_lookup=_const_price(10.0),
        fx_lookup=lambda s, t, on: 1.0, current_qty={"AAA": 10.0},
        current_cash_base=900.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 2),
    )
    m = _series_to_map(series)
    assert m["2024-01-01"]["cash"] == 900.0     # 1000 deposit - 100 buy
    assert m["2024-01-02"]["cash"] == 900.0
    assert m["2024-01-01"]["total"] == 1000.0   # 900 cash + 100 holdings


def test_cash_anchor_offset_corrects_for_unmodelled_flows():
    # Real cash today is 950 (e.g. £50 of dividends/interest we didn't model);
    # the whole cash curve shifts by +50 so the right edge matches reality.
    fills = [Fill(on=date(2024, 1, 1), key="AAA", qty=10.0, cash_impact=-100.0,
                  account_ccy="USD", instrument_ccy="USD")]
    cash = [CashEvent(on=date(2024, 1, 1), amount=1000.0, ccy="USD")]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=cash, price_lookup=_const_price(10.0),
        fx_lookup=lambda s, t, on: 1.0, current_qty={"AAA": 10.0},
        current_cash_base=950.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 2),
    )
    m = _series_to_map(series)
    assert m["2024-01-02"]["cash"] == 950.0   # raw 900 + offset 50
    assert m["2024-01-01"]["cash"] == 950.0


def test_normalize_split_basis_scales_pre_split_fills_to_today_units():
    # yfinance prices are split-adjusted; T212 fills are as-executed. Scale each
    # fill to today's basis so shares × adjusted-price is consistent.
    from app.services.equity_reconstruction import normalize_split_basis

    fills = [
        Fill(on=date(2020, 1, 1), key="AAPL", qty=10.0, cash_impact=-1000.0,
             account_ccy="USD", instrument_ccy="USD"),   # before a 4:1 split
        Fill(on=date(2021, 1, 1), key="AAPL", qty=5.0, cash_impact=-500.0,
             account_ccy="USD", instrument_ccy="USD"),    # after the split
        Fill(on=date(2020, 12, 28), key="PTN", qty=400.0, cash_impact=-400.0,
             account_ccy="USD", instrument_ccy="USD"),    # before a 1:50 reverse split
    ]
    splits = {
        "AAPL": [(date(2020, 8, 31), 4.0)],     # 4:1 forward → pre-split shares ×4
        "PTN": [(date(2024, 6, 1), 0.02)],      # 1:50 reverse → pre-split shares ×0.02
    }
    out = {(f.key, f.on): f for f in normalize_split_basis(fills, splits)}

    assert out[("AAPL", date(2020, 1, 1))].qty == 40.0   # ×4 (split is after it)
    assert out[("AAPL", date(2021, 1, 1))].qty == 5.0    # unchanged (split before it)
    assert out[("PTN", date(2020, 12, 28))].qty == 8.0   # ×0.02
    # cash impact is in money terms — splits never change it
    assert out[("AAPL", date(2020, 1, 1))].cash_impact == -1000.0


def test_to_today_basis_divides_pre_split_prices_by_later_splits():
    # Unadjusted closes → today's basis: a close before a 4:1 split is divided by
    # 4 (so shares×price stays invariant); a close after it is unchanged. With no
    # split data, prices pass through untouched (the graceful fallback for ETPs).
    from app.services.equity_backfill import _to_today_basis

    rows = [(date(2020, 1, 1), 400.0), (date(2022, 1, 1), 100.0)]
    adj = _to_today_basis(rows, [(date(2021, 1, 1), 4.0)])
    assert adj.on(date(2020, 6, 1)) == 100.0   # 400 / 4 (split is later)
    assert adj.on(date(2022, 6, 1)) == 100.0   # unchanged (split before it)

    passthrough = _to_today_basis(rows, [])     # no splits → real prices kept
    assert passthrough.on(date(2020, 6, 1)) == 400.0


def test_normalize_orders_keys_by_code_and_signs_by_side():
    # T212's orders feed leaves instrument.isin EMPTY for most US equities, so
    # the stable join key with current positions is the instrument CODE, not ISIN.
    from app.services.equity_backfill import _normalize_orders

    orders = [
        {  # SELL → negative shares, cash IN. Note: empty isin (the real-world case).
            "order": {"side": "SELL", "status": "FILLED", "ticker": "PLTR_US_EQ",
                      "currency": "USD", "filledValue": -2000.0,
                      "instrument": {"ticker": "PLTR_US_EQ", "isin": "", "currency": "USD"}},
            "fill": {"quantity": -3.25, "price": 614.56, "filledAt": "2026-06-01T15:46:48.000Z",
                     "walletImpact": {"currency": "USD", "netValue": 2000.0}},
        },
        {  # BUY → positive shares, cash OUT
            "order": {"side": "BUY", "status": "FILLED", "ticker": "AAPL_US_EQ",
                      "currency": "USD",
                      "instrument": {"ticker": "AAPL_US_EQ", "isin": "US0378331005", "currency": "USD"}},
            "fill": {"quantity": 5.0, "price": 100.0, "filledAt": "2025-03-10T14:00:00.000Z",
                     "walletImpact": {"currency": "USD", "netValue": -500.0}},
        },
    ]
    fills, meta = _normalize_orders(orders, "USD")

    sell = next(f for f in fills if f.key == "PLTR_US_EQ")  # keyed by code despite empty isin
    assert sell.qty < 0                 # sell reduces shares
    assert sell.cash_impact == 2000.0   # sell brings cash in
    buy = next(f for f in fills if f.key == "AAPL_US_EQ")
    assert buy.qty == 5.0               # buy adds shares
    assert buy.cash_impact == -500.0    # buy takes cash out
    assert "PLTR_US_EQ" in meta and meta["PLTR_US_EQ"]["code"] == "PLTR_US_EQ"


def test_normalize_dividends_become_positive_cash_on_paid_date():
    from app.services.equity_backfill import _normalize_dividends

    events = _normalize_dividends([
        {"amount": 17.24, "currency": "USD", "paidOn": "2026-04-09T17:23:25.000+03:00"},
        {"amount": 0.0, "currency": "USD", "paidOn": "2026-01-01T00:00:00Z"},  # dropped
    ])
    assert len(events) == 1
    assert events[0].amount == 17.24
    assert events[0].on == date(2026, 4, 9)


def test_unpriceable_instrument_contributes_zero_not_crash():
    # price_lookup returns None (e.g. delisted/unmappable ticker) → that
    # instrument is skipped for the day rather than blowing up.
    fills = [Fill(on=date(2024, 1, 1), key="DEAD", qty=5.0, cash_impact=-50.0,
                  account_ccy="USD", instrument_ccy="USD")]
    series = reconstruct_daily_equity(
        fills=fills, cash_events=[], price_lookup=lambda key, on: None,
        fx_lookup=lambda s, t, on: 1.0, current_qty={"DEAD": 5.0},
        current_cash_base=0.0, target_ccy="GBP",
        start=date(2024, 1, 1), end=date(2024, 1, 1),
    )
    assert series[0]["holdings"] == 0.0
