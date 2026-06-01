"""Tests for the autopilot portfolio optimiser (pure planner)."""

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.portfolio_optimizer import plan_rebalance


def _pos(ticker, value, price=100.0, account="invest"):
    return {
        "ticker": ticker,
        "instrument_code": f"{ticker}_US_EQ",
        "name": ticker,
        "account_kind": account,
        "value": value,
        "current_price": price,
    }


def test_no_breach_no_trades():
    # Balanced book under a 25% cap → nothing to do.
    positions = [_pos("A", 250), _pos("B", 250), _pos("C", 250), _pos("D", 250)]
    plan = plan_rebalance(positions, 1000.0, {"max_position_weight": 0.25})
    assert plan["trades"] == []
    assert "No action needed" in plan["rationale"]


def test_single_breach_trims_to_cap_into_cash():
    # A is 50% of a 1000 book; B and C sit exactly at the 25% cap → only A trims.
    positions = [_pos("A", 500), _pos("B", 250), _pos("C", 250)]
    plan = plan_rebalance(positions, 1000.0, {"max_position_weight": 0.25, "turnover_budget_pct": 0.5})
    sells = [t for t in plan["trades"] if t["side"] == "sell"]
    assert len(sells) == 1
    s = sells[0]
    assert s["ticker"] == "A"
    assert abs(s["est_notional"] - 250.0) < 1e-6
    assert abs(s["quantity"] - 2.5) < 1e-6  # 250 / 100
    assert s["target_weight"] == 0.25
    # No buys by default (freed → cash).
    assert all(t["side"] == "sell" for t in plan["trades"])
    # Top weight projected down to the cap.
    assert plan["after"]["top_position_weight"] <= 0.25 + 1e-6
    assert plan["after"]["concentration_hhi"] < plan["before"]["concentration_hhi"]


def test_turnover_budget_partial_trim_by_default():
    # With the default 20% turnover budget, a 50% breach is only partially
    # trimmed in one pass — autopilot rebalances gradually, not all at once.
    positions = [_pos("A", 500), _pos("B", 250), _pos("C", 250)]
    plan = plan_rebalance(positions, 1000.0, {"max_position_weight": 0.25})
    sell = [t for t in plan["trades"] if t["side"] == "sell"][0]
    assert abs(sell["est_notional"] - 200.0) < 1e-6  # capped at 20% of 1000


def test_min_trade_filters_tiny_excess():
    # A is fractionally over cap → excess below min_trade → skipped.
    positions = [_pos("A", 260), _pos("B", 250), _pos("C", 245), _pos("D", 245)]
    plan = plan_rebalance(positions, 1000.0, {"max_position_weight": 0.25, "min_trade_gbp": 75})
    assert plan["trades"] == []  # excess £10 < £75


def test_turnover_budget_caps_total_traded():
    # Two big breaches but a tight 10% turnover budget caps total sold.
    positions = [_pos("A", 500), _pos("B", 400), _pos("C", 100)]
    plan = plan_rebalance(
        positions, 1000.0,
        {"max_position_weight": 0.25, "min_trade_gbp": 10, "turnover_budget_pct": 0.10},
    )
    total_sold = sum(t["est_notional"] for t in plan["trades"] if t["side"] == "sell")
    assert total_sold <= 100.0 + 1e-6  # 10% of 1000


def test_redistribute_buys_into_underweight():
    positions = [_pos("A", 600), _pos("B", 250), _pos("C", 150)]
    plan = plan_rebalance(
        positions, 1000.0,
        {"max_position_weight": 0.30, "min_trade_gbp": 10, "redistribute": True, "turnover_budget_pct": 1.0},
    )
    sells = [t for t in plan["trades"] if t["side"] == "sell"]
    buys = [t for t in plan["trades"] if t["side"] == "buy"]
    assert sells and buys
    # Freed £300 (600→300 cap) redistributed; smallest (C) gets topped up.
    assert any(t["ticker"] == "C" for t in buys)


def test_per_name_cap_override():
    # Global cap 30% but PLTR override 20% → trim PLTR harder.
    positions = [_pos("PLTR", 280), _pos("B", 360), _pos("C", 360)]
    plan = plan_rebalance(
        positions, 1000.0,
        {"max_position_weight": 0.30, "per_name_caps": {"PLTR": 0.20}, "min_trade_gbp": 10},
    )
    pltr = [t for t in plan["trades"] if t["ticker"] == "PLTR"]
    assert pltr and pltr[0]["target_weight"] == 0.20
    assert abs(pltr[0]["est_notional"] - 80.0) < 1e-6  # 280 - 0.20*1000
