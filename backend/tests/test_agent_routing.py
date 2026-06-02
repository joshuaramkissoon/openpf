"""Tests for de-duplicating + account-routing proposed trade intents."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.agent_service import ProposedIntent, _dedupe_and_route, _size_add


def _sz(**kw):
    base = dict(total_book=100000.0, current_weight=0.05, max_weight=0.25,
                momentum=0.20, trend=1.0, rsi=55.0, volatility=0.25,
                max_single=500.0, free_cash=1_000_000.0)
    base.update(kw)
    return _size_add(**base)


def test_size_add_scales_with_conviction():
    strong = _sz(momentum=0.30, trend=1.0)
    weak = _sz(momentum=0.0, trend=0.0)
    assert strong > weak > 0


def test_size_add_shrinks_as_position_approaches_cap():
    far = _sz(current_weight=0.02)   # lots of room to the 25% cap
    near = _sz(current_weight=0.23)  # almost at cap
    assert far > near


def test_size_add_shrinks_with_volatility():
    calm = _sz(volatility=0.20)
    wild = _sz(volatility=0.60)
    assert calm > wild


def test_size_add_never_exceeds_single_order_cap():
    # Max conviction, max room, low vol, infinite cash → clamps to the cap.
    n = _sz(momentum=0.5, trend=1.0, rsi=40, volatility=0.10,
            current_weight=0.0, max_single=500.0)
    assert n <= 500.0 + 1e-9
    assert n > 400.0  # and gets close to it


def test_size_add_capped_by_free_cash():
    assert _sz(free_cash=120.0, momentum=0.5, trend=1.0, current_weight=0.0) == 120.0


def test_size_add_zero_at_or_above_cap():
    assert _sz(current_weight=0.25) == 0.0
    assert _sz(current_weight=0.30) == 0.0


def test_size_add_zero_when_below_min_trade():
    # Almost no room + weak signal → sub-£25 → not worth proposing.
    assert _sz(current_weight=0.248, momentum=0.0, trend=0.0) == 0.0


def _buy(symbol, notional, conf=0.9, account="invest"):
    return ProposedIntent(
        symbol=symbol, instrument_code=f"{symbol}_US_EQ", side="buy", order_type="market",
        quantity=1.0, estimated_notional=notional, expected_edge=0.01, confidence=conf,
        risk_score=0.3, rationale="add", metadata={"account_kind": account},
    )


def _snapshot(isa_cash, positions):
    return {
        "accounts": [
            {"account_kind": "stocks_isa", "free_cash": isa_cash},
            {"account_kind": "invest", "free_cash": 999999.0},
        ],
        "positions": positions,
    }


def test_same_symbol_buy_in_both_accounts_collapses_to_one():
    ideas = [_buy("NVDA", 3000, account="invest"), _buy("NVDA", 3000, account="stocks_isa")]
    out = _dedupe_and_route(ideas, _snapshot(isa_cash=10000, positions=[]))
    assert len(out) == 1
    assert out[0].symbol == "NVDA"


def test_buy_prefers_isa_when_it_has_the_cash():
    ideas = [_buy("NVDA", 3000)]
    out = _dedupe_and_route(ideas, _snapshot(isa_cash=10000, positions=[]))
    assert out[0].metadata["account_kind"] == "stocks_isa"


def test_buy_falls_back_to_invest_when_isa_cash_short():
    ideas = [_buy("NVDA", 3000)]
    out = _dedupe_and_route(ideas, _snapshot(isa_cash=500, positions=[]))
    assert out[0].metadata["account_kind"] == "invest"


def test_isa_cash_is_consumed_greedily_across_buys():
    # ISA has room for one £3k buy, not two → first ISA, second Invest.
    ideas = [_buy("AAA", 3000), _buy("BBB", 3000)]
    out = _dedupe_and_route(ideas, _snapshot(isa_cash=3500, positions=[]))
    routed = {i.symbol: i.metadata["account_kind"] for i in out}
    assert routed == {"AAA": "stocks_isa", "BBB": "invest"}


def test_sell_routes_to_account_holding_the_most():
    sell = ProposedIntent(
        symbol="TSLA", instrument_code="TSLA_US_EQ", side="sell", order_type="market",
        quantity=1.0, estimated_notional=1000, expected_edge=0.01, confidence=0.8,
        risk_score=0.4, rationale="trim", metadata={"account_kind": "invest"},
    )
    snap = _snapshot(isa_cash=0, positions=[
        {"ticker": "TSLA", "account_kind": "stocks_isa", "value": 9000.0},
        {"ticker": "TSLA", "account_kind": "invest", "value": 1000.0},
    ])
    out = _dedupe_and_route([sell], snap)
    assert out[0].metadata["account_kind"] == "stocks_isa"
