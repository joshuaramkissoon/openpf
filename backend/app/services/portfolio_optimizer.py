"""Autopilot portfolio optimiser / rebalancer.

Manages the *long-term core book* (Invest + ISA equity holdings) — distinct from
the short-term leveraged engine. The design goal is **autopilot**: Archie owns
the objective (read from a structured policy with sane defaults), proactively
proposes a concrete rebalance, and the operator just approves. No knobs to set
per-run; tuning is a one-line policy change Archie can make when the user says
so in chat.

The default objective is deliberately the *least-opinionated* useful action:
**enforce concentration caps with minimum turnover, trimming breaches to cash**
(raising dry powder rather than making fresh buy decisions the user didn't
sanction). Optional `redistribute` tops the freed capital back into the most
under-weight existing holdings instead of cash.

`plan_rebalance` is pure (positions + policy → plan) so it's fully unit-testable
with no DB or network. `compute_rebalance` is the thin DB-backed wrapper.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.services.analytics import concentration_hhi

logger = logging.getLogger(__name__)

# Sane zero-config defaults. The optimiser produces useful proposals with none
# of these touched; Archie adjusts them (per the user's stated preferences) via
# set_rebalance — there is no per-run form.
REBALANCE_DEFAULT: dict[str, Any] = {
    "enabled": True,
    # Hard ceiling on any single name's share of an account (fraction).
    "max_position_weight": 0.25,
    # Per-name overrides, e.g. {"PLTR": 0.22}. Empty = use the global cap.
    "per_name_caps": {},
    # Skip trims smaller than this (avoid dust trades), in GBP.
    "min_trade_gbp": 75.0,
    # Cap total value traded in one rebalance as a fraction of the account
    # (keeps proposals to a few meaningful trades, not a wholesale reshuffle).
    "turnover_budget_pct": 0.20,
    # Tolerance band so we don't churn on a name sitting fractionally over cap.
    "tolerance_pct": 0.01,
    # When True, freed proceeds top up the most under-weight holdings; when
    # False (default), they go to cash — the least-opinionated action.
    "redistribute": False,
}


def _effective_cap(ticker: str, policy: dict[str, Any]) -> float:
    overrides = policy.get("per_name_caps") or {}
    if isinstance(overrides, dict) and ticker.upper() in {k.upper() for k in overrides}:
        for k, v in overrides.items():
            if k.upper() == ticker.upper():
                try:
                    return max(0.01, min(1.0, float(v)))
                except (TypeError, ValueError):
                    break
    try:
        return max(0.01, min(1.0, float(policy.get("max_position_weight", 0.25))))
    except (TypeError, ValueError):
        return 0.25


def _risk_snapshot(positions: list[dict[str, Any]], total_value: float) -> dict[str, Any]:
    """Concentration metrics for a set of {value} positions over total_value."""
    rows = [{"weight": (float(p.get("value") or 0.0) / total_value) if total_value else 0.0} for p in positions]
    top = max((r["weight"] for r in rows), default=0.0)
    return {
        "concentration_hhi": round(concentration_hhi(rows), 4),
        "top_position_weight": round(top, 4),
        "names": len([p for p in positions if float(p.get("value") or 0.0) > 0]),
    }


def plan_rebalance(
    positions: list[dict[str, Any]],
    total_value: float,
    policy: dict[str, Any] | None = None,
    *,
    account_kind: str = "all",
) -> dict[str, Any]:
    """Pure rebalance planner.

    `positions`: list of dicts with at least ticker, instrument_code, value,
    current_price, account_kind (and optionally name). `total_value` is the
    account's total equity (positions + cash). Returns a structured plan with
    the trades, before/after risk, and a plain-English rationale.
    """
    policy = {**REBALANCE_DEFAULT, **(policy or {})}
    min_trade = float(policy["min_trade_gbp"])
    tol = float(policy["tolerance_pct"])
    turnover_cap = float(policy["turnover_budget_pct"]) * total_value if total_value else 0.0

    before = _risk_snapshot(positions, total_value)

    # 1. Find cap breaches and the excess £ to trim on each (largest first).
    breaches: list[dict[str, Any]] = []
    for p in positions:
        value = float(p.get("value") or 0.0)
        price = float(p.get("current_price") or 0.0)
        if value <= 0 or price <= 0 or total_value <= 0:
            continue
        weight = value / total_value
        cap = _effective_cap(str(p.get("ticker") or ""), policy)
        if weight > cap + tol:
            excess = value - cap * total_value
            if excess >= min_trade:
                breaches.append({"pos": p, "weight": weight, "cap": cap, "excess": excess, "price": price})
    breaches.sort(key=lambda b: b["excess"], reverse=True)

    # 2. Build SELL trades within the turnover budget.
    trades: list[dict[str, Any]] = []
    traded = 0.0
    freed = 0.0
    for b in breaches:
        if turnover_cap and traded >= turnover_cap:
            break
        notional = b["excess"]
        if turnover_cap:
            notional = min(notional, turnover_cap - traded)
        if notional < min_trade:
            continue
        p = b["pos"]
        qty = round(notional / b["price"], 4)
        if qty <= 0:
            continue
        trades.append({
            "account_kind": p.get("account_kind", account_kind),
            "ticker": p.get("ticker"),
            "instrument_code": p.get("instrument_code"),
            "name": p.get("name"),
            "side": "sell",
            "quantity": qty,
            "est_notional": round(notional, 2),
            "current_weight": round(b["weight"], 4),
            "target_weight": round(b["cap"], 4),
            "reason": f"Trim {p.get('ticker')} from {b['weight']:.1%} to its {b['cap']:.0%} cap",
        })
        traded += notional
        freed += notional

    # 3. Optional redistribute: top up the most under-weight holdings with the
    #    freed proceeds (still within turnover budget). Off by default.
    if policy.get("redistribute") and freed >= min_trade and total_value > 0:
        sold = {t["ticker"] for t in trades}
        under = [
            p for p in positions
            if p.get("ticker") not in sold
            and float(p.get("current_price") or 0.0) > 0
            and (float(p.get("value") or 0.0) / total_value) < _effective_cap(str(p.get("ticker") or ""), policy)
        ]
        # Smallest weight first (bring the laggards up).
        under.sort(key=lambda p: float(p.get("value") or 0.0))
        per = freed / len(under) if under else 0.0
        for p in under:
            if per < min_trade:
                break
            price = float(p["current_price"])
            qty = round(per / price, 4)
            if qty <= 0:
                continue
            trades.append({
                "account_kind": p.get("account_kind", account_kind),
                "ticker": p.get("ticker"),
                "instrument_code": p.get("instrument_code"),
                "name": p.get("name"),
                "side": "buy",
                "quantity": qty,
                "est_notional": round(per, 2),
                "current_weight": round(float(p.get("value") or 0.0) / total_value, 4),
                "target_weight": None,
                "reason": f"Redistribute freed capital into under-weight {p.get('ticker')}",
            })

    # 4. Project the after-trim risk (sells reduce the breached names to cap).
    after_positions = []
    sell_by_ticker = {t["ticker"]: t["est_notional"] for t in trades if t["side"] == "sell"}
    for p in positions:
        v = float(p.get("value") or 0.0) - sell_by_ticker.get(p.get("ticker"), 0.0)
        after_positions.append({**p, "value": max(v, 0.0)})
    after = _risk_snapshot(after_positions, total_value)

    if not trades:
        rationale = (
            f"No action needed — all holdings are within their concentration caps "
            f"(top {before['top_position_weight']:.1%}, HHI {before['concentration_hhi']})."
        )
    else:
        n_sells = sum(1 for t in trades if t["side"] == "sell")
        dest = "redistributed into under-weight holdings" if policy.get("redistribute") else "raised as cash"
        rationale = (
            f"{n_sells} holding(s) over their concentration cap. Trimming ~£{freed:,.0f} "
            f"({dest}) cuts top-weight {before['top_position_weight']:.1%} → {after['top_position_weight']:.1%} "
            f"and HHI {before['concentration_hhi']} → {after['concentration_hhi']}."
        )

    return {
        "account_kind": account_kind,
        "objective": "constraint",
        "total_value": round(total_value, 2),
        "trades": trades,
        "turnover": round(traded + sum(t["est_notional"] for t in trades if t["side"] == "buy"), 2),
        "turnover_budget": round(turnover_cap, 2),
        "before": before,
        "after": after,
        "rationale": rationale,
        "policy": {k: policy[k] for k in ("max_position_weight", "per_name_caps", "min_trade_gbp", "turnover_budget_pct", "redistribute")},
    }


def _aggregate_by_ticker(positions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse per-account position rows into one row per ticker.

    Concentration is a whole-book concept (e.g. PLTR held in BOTH Invest and ISA
    is one 28% bet, not two sub-cap ones). We sum value across accounts and pin
    the trade to the account holding the MOST of that ticker (where a trim is
    actually executable).
    """
    by_ticker: dict[str, dict[str, Any]] = {}
    for p in positions:
        ticker = str(p.get("ticker") or "").upper()
        if not ticker:
            continue
        value = float(p.get("value") or 0.0)
        agg = by_ticker.get(ticker)
        if agg is None:
            by_ticker[ticker] = {**p, "ticker": ticker, "value": value, "_dominant_value": value}
        else:
            agg["value"] += value
            # Keep the account/instrument/price of the largest holding for the trade.
            if value > agg["_dominant_value"]:
                agg["_dominant_value"] = value
                agg["account_kind"] = p.get("account_kind")
                agg["instrument_code"] = p.get("instrument_code")
                agg["current_price"] = p.get("current_price")
                agg["name"] = p.get("name")
    for agg in by_ticker.values():
        agg.pop("_dominant_value", None)
    return list(by_ticker.values())


def compute_rebalance(db: Session, account_kind: str = "all", policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """DB-backed wrapper: load the live snapshot and plan a rebalance.

    Rebalances WITHIN an account by default (no cross-account moves); pass
    "invest" or "stocks_isa" to scope to one. Reads the policy from ConfigStore
    when not supplied.
    """
    from app.services.config_store import ConfigStore
    from app.services.portfolio_service import get_portfolio_snapshot

    if policy is None:
        policy = ConfigStore(db).get("rebalance_config", REBALANCE_DEFAULT)

    snap = get_portfolio_snapshot(db, account_kind=account_kind, display_currency="GBP")
    positions = snap.get("positions", [])
    # Measure concentration on the whole-book ticker exposure (PLTR across both
    # accounts is one bet), and pin trims to the account holding the most.
    positions = _aggregate_by_ticker(positions)
    total_value = float(snap.get("account", {}).get("total") or 0.0)
    if total_value <= 0:
        # Fall back to summing position values + free cash.
        total_value = sum(float(p.get("value") or 0.0) for p in positions) + float(
            snap.get("account", {}).get("free_cash") or 0.0
        )
    return plan_rebalance(positions, total_value, policy, account_kind=account_kind)


def propose_rebalance(db: Session, account_kind: str = "all") -> dict[str, Any]:
    """Compute a rebalance and materialise its trades as PROPOSED intents.

    The trades land in the normal Execution queue as ``status="proposed"`` — the
    same one-tap approve/reject flow as every other proposal. Nothing executes
    here; the operator (or Archie, with auto-execute) approves. Returns the plan
    plus the created intent ids.
    """
    from app.models.entities import TradeIntent
    from app.services.config_store import ConfigStore

    plan = compute_rebalance(db, account_kind=account_kind)
    broker_mode = ConfigStore(db).get_broker().get("broker_mode", "paper")

    created: list[str] = []
    for t in plan["trades"]:
        intent = TradeIntent(
            status="proposed",
            broker_mode=broker_mode,
            symbol=t.get("ticker"),
            instrument_code=t.get("instrument_code"),
            side=t.get("side"),
            order_type="market",
            quantity=float(t.get("quantity") or 0.0),
            estimated_notional=float(t.get("est_notional") or 0.0),
            expected_edge=0.0,
            confidence=0.7,
            risk_score=0.3,
            rationale=f"[rebalance] {t.get('reason')}",
            meta={
                "source": "rebalance",
                "account_kind": t.get("account_kind"),
                "current_weight": t.get("current_weight"),
                "target_weight": t.get("target_weight"),
            },
        )
        db.add(intent)
        db.flush()
        created.append(intent.id)
    db.commit()

    plan["proposed_intent_ids"] = created
    plan["proposed_count"] = len(created)
    return plan
