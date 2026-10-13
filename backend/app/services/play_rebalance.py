"""Rebalance a play's holdings back to its target weights.

``plan`` is the deterministic part: it measures each leg's share of what the
play holds, asks ``app.quant.rebalance.rebalance_plan`` which legs are out of
band (drift under ``MIN_DRIFT`` is ignored), then turns the £ moves into
per-account orders. Sells come from the accounts that hold the leg (the play's
own account first); buys go into the play's account, funded by the sales that
land there (plus free cash only when ``use_cash``), and spill into another
account only where a sale freed cash in it. Nothing is ever bought with money
an account doesn't have. Trades under ``MIN_TRADE_GBP`` are dropped.

Two ways to act on a plan, both recomputed server-side from the saved targets
(the client never sends trades):

* ``propose`` records ``proposed`` intents through ``propose_trade`` with the
  rebalancer's reserved source "rebalance" — every rail checked, a human approves.
* ``place`` is the user placing them by hand: ``manual_trade.place`` per order
  (advisory limits, live confirmation). Agents have no route here: no MCP tool,
  and their hooks block the endpoint and this module.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.entities import Play
from app.quant import rebalance as qrebalance

logger = logging.getLogger(__name__)

MIN_DRIFT = 0.01  # ignore legs within 1 point of target
MIN_TRADE_GBP = 25.0  # and any order smaller than this
FX_FEE_RATE = 0.0015  # T212: no commission; 0.15% FX on non-GBP instruments
_GBP = {"GBP", "GBX"}


class RebalanceError(ValueError):
    pass


def _norm_weights(legs: list[dict[str, Any]], weights: dict[str, float] | None) -> dict[str, float]:
    raw = {}
    for leg in legs:
        t = leg["ticker"]
        w = (weights or {}).get(t, leg.get("target_weight") or 0.0)
        raw[t] = max(0.0, float(w or 0.0))
    total = sum(raw.values())
    if total <= 0:
        raise RebalanceError("Give at least one leg a target weight above 0%.")
    return {t: w / total for t, w in raw.items()}


def _price_gbp(ticker: str, currency: str | None, price_fn: Callable[[str], float | None]) -> float | None:
    """Last close in GBP per share (for a leg the play doesn't hold yet)."""
    from app.services.fx import get_fx_rate
    from app.services.plays_service import listing_currency

    px = price_fn(ticker)
    if not px:
        return None
    ccy = (currency or listing_currency(ticker)).upper()
    if ccy == "GBX":
        return px / 100.0  # London lines quote in pence
    try:
        return px * get_fx_rate(ccy, "GBP")
    except Exception:  # noqa: BLE001
        return None


def plan(
    db: Session,
    play: Play,
    *,
    weights: dict[str, float] | None = None,
    use_cash: bool = False,
    book: dict[str, Any] | None = None,
    owned: list[dict[str, Any]] | None = None,
    price_fn: Callable[[str], float | None] | None = None,
    min_drift: float = MIN_DRIFT,
    min_trade_gbp: float = MIN_TRADE_GBP,
) -> dict[str, Any]:
    """The trades that bring the play's legs to target. Records nothing."""
    from app.services import plays_service as ps

    legs = list(play.legs or [])
    if not legs:
        raise RebalanceError("This play has no basket yet.")
    target = _norm_weights(legs, weights)
    book = book or ps.book_context(db)
    if owned is None:
        from app.services import book_service

        owned = book_service.owned_positions(db, play, book["positions"])
    price_fn = price_fn or ps.last_price
    acct = play.account_kind
    free_cash = {k: float(v or 0.0) for k, v in (book.get("free_cash_gbp") or {}).items()}

    # What the play holds in each leg, per account.
    holdings: dict[str, list[dict[str, Any]]] = {}
    for leg in legs:
        keys = ps._leg_keys(leg)
        holdings[leg["ticker"]] = [p for p in owned if ps._position_matches(p, keys) and p.get("value", 0) > 0]
    value = {t: sum(p["value"] for p in rows) for t, rows in holdings.items()}
    total = sum(value.values())
    by_ticker = {l["ticker"]: l for l in legs}

    base = {
        "play_id": play.id, "account_kind": acct, "value_gbp": round(total, 2), "use_cash": use_cash,
        "min_drift": min_drift, "min_trade_gbp": min_trade_gbp,
        "free_cash_gbp": round(free_cash.get(acct, 0.0), 2),
    }
    if total <= 0:
        return {**base, "legs": [
            {"ticker": t, "name": by_ticker[t].get("name") or "", "value_gbp": 0.0, "current_weight": 0.0,
             "target_weight": round(target[t], 4), "after_weight": 0.0, "drift": -round(target[t], 4), "action": "hold"}
            for t in target], "trades": [], "ignored": [], "turnover_gbp": 0.0, "est_fees_gbp": 0.0,
            "cash_used_gbp": 0.0, "warnings": [],
            "empty_reason": "Nothing in this play is held yet, so there's nothing to rebalance. Stage a tranche instead."}

    current = {t: v / total for t, v in value.items()}
    cash = 0.0
    cash_binding = False
    deferred: list[str] = []
    if use_cash:
        # Buy-only: top the underweight legs up with free cash instead of selling.
        # Never more than it takes to put the most overweight leg back on target.
        full = max(value[t] / target[t] for t in target if target[t] > 0)
        needed = sum(max(0.0, target[t] * full - value[t]) for t in target)
        cash = min(free_cash.get(acct, 0.0), needed)
        cash_binding = cash < needed - 1
        grown = total + cash
        gaps = {t: max(0.0, target[t] * grown - value[t]) for t in target if target[t] - current[t] > min_drift}
        scale = cash / sum(gaps.values()) if sum(gaps.values()) > 0 else 0.0
        moves = {t: g * scale for t, g in gaps.items() if g * scale >= min_trade_gbp}
        deferred = [t for t, g in gaps.items() if 0 < g * scale < min_trade_gbp]
        cash = sum(moves.values())
    else:
        qplan = qrebalance.rebalance_plan(
            current, target, total, abs_band=min_drift, rel_band=1.0, trade_to="target",
            turnover_budget=None, cash_available_gbp=0.0, min_trade_gbp=min_trade_gbp,
        )
        moves = {r.ticker: r.notional_gbp for r in qplan.trades}
        cash_binding, deferred = qplan.cash_binding, qplan.deferred

    # Sells: from the accounts that hold the leg, the play's own account first.
    trades: list[dict[str, Any]] = []
    pools: dict[str, float] = {acct: cash}
    for t, amt in sorted(moves.items(), key=lambda kv: kv[1]):
        if amt >= 0:
            continue
        left = -amt
        for p in sorted(holdings[t], key=lambda p: (p.get("account_kind") != acct, -p["value"])):
            if left <= 0.005:
                break
            take = min(left, p["value"])
            qty = float(p.get("quantity") or 0.0)
            unit = p["value"] / qty if qty > 0 else None
            all_out = take >= p["value"] * 0.995
            trades.append({
                "ticker": t, "side": "sell", "account_kind": p.get("account_kind") or acct,
                "notional_gbp": round(p["value"] if all_out else take, 2),
                "shares": round(qty if all_out else (take / unit), 6) if unit else None,
                "price_gbp": round(unit, 4) if unit else None, "sell_all": all_out,
                "instrument_code": p.get("instrument_code"), "currency": p.get("currency") or by_ticker[t].get("currency"),
            })
            k = p.get("account_kind") or acct
            pools[k] = pools.get(k, 0.0) + take
            left -= take

    # Buys: into the play's account first, then wherever a sale freed cash.
    warnings: list[str] = []
    unfunded = 0.0
    for t, amt in sorted(moves.items(), key=lambda kv: -kv[1]):
        if amt <= 0:
            continue
        leg = by_ticker[t]
        held = holdings[t]
        unit = None
        for p in held:
            if p.get("quantity"):
                unit = p["value"] / float(p["quantity"])
                break
        if unit is None:
            unit = _price_gbp(t, leg.get("currency"), price_fn)
        left = amt
        order = [acct] + sorted((k for k in pools if k != acct), key=lambda k: -pools[k])
        for k in order:
            avail = pools.get(k, 0.0)
            if left <= 0.005 or avail <= 0.005:
                continue
            if (acct == "crypto") != (k == "crypto"):
                continue  # crypto proceeds never fund shares, or the reverse
            take = min(left, avail)
            trades.append({
                "ticker": t, "side": "buy", "account_kind": k, "notional_gbp": round(take, 2),
                "shares": round(take / unit, 6) if unit else None, "price_gbp": round(unit, 4) if unit else None,
                "sell_all": False,
                "instrument_code": leg.get("instrument_code") or (held[0].get("instrument_code") if held else None),
                "currency": leg.get("currency") or (held[0].get("currency") if held else None) or ps.listing_currency(t),
                "note": None if k == acct else f"bought in {_acct(k)} with the sale proceeds there",
            })
            pools[k] = avail - take
            left -= take
        if left > 0.5:
            unfunded += left

    dropped = [t for t in trades if t["notional_gbp"] < min_trade_gbp]
    trades = [t for t in trades if t["notional_gbp"] >= min_trade_gbp]
    if unfunded >= 1:
        warnings.append(
            f"£{unfunded:,.0f} of buys had no cash to fund them in the right account, so they're left out. "
            + ("Add cash to the account, or top up with cash instead." if not use_cash else "Add cash to the account."))
    wanted = sum(max(0.0, target[t] - current[t]) * total for t in target if target[t] - current[t] > min_drift)
    funded = sum(tr["notional_gbp"] for tr in trades if tr["side"] == "buy")
    if cash_binding and wanted - funded > max(min_trade_gbp, 0.02 * wanted):
        warnings.append("Not enough free cash to reach every target; the buys share what there is."
                        if use_cash else "Buys were scaled down to what the sales can fund.")

    after_val = dict(value)
    for tr in trades:
        after_val[tr["ticker"]] += tr["notional_gbp"] if tr["side"] == "buy" else -tr["notional_gbp"]
    after_total = sum(after_val.values()) or 1.0
    for tr in trades:
        ccy = str(tr.get("currency") or "").upper()
        tr["fee_gbp"] = 0.0 if ccy in _GBP or tr["account_kind"] == "crypto" else round(tr["notional_gbp"] * FX_FEE_RATE, 2)
        tr["name"] = by_ticker[tr["ticker"]].get("name") or ""
    trades.sort(key=lambda tr: (tr["side"] != "sell", -tr["notional_gbp"]))

    try:
        from app.services.config_store import ConfigStore

        cap = float(ConfigStore(db).get_risk().get("max_single_order_notional") or 0.0)
    except Exception:  # noqa: BLE001
        cap = 0.0
    over = [tr for tr in trades if cap and tr["notional_gbp"] > cap]
    for tr in trades:
        tr["over_order_limit"] = tr in over
    if over:
        warnings.append(
            f"{len(over)} order{'s are' if len(over) > 1 else ' is'} over your £{cap:,.0f} per-order limit: proposing holds "
            f"{'them' if len(over) > 1 else 'it'} back (and the buys {'they fund' if len(over) > 1 else 'it funds'}); "
            "placing them yourself treats the limit as a warning.")

    moved = {tr["ticker"] for tr in trades}
    legs_out = []
    ignored = []
    for t in target:
        drift = current[t] - target[t]
        if t not in moved and abs(drift) >= 0.0005:
            ignored.append({"ticker": t, "drift": round(drift, 4),
                            "reason": "within 1 point of target" if abs(drift) <= min_drift + 1e-9 else
                            ("under £%d" % min_trade_gbp if t in {d["ticker"] for d in dropped} or t in deferred
                             else "no cash to fund it")})
        legs_out.append({
            "ticker": t, "name": by_ticker[t].get("name") or "", "value_gbp": round(value[t], 2),
            "after_value_gbp": round(after_val[t], 2), "current_weight": round(current[t], 4),
            "target_weight": round(target[t], 4), "after_weight": round(after_val[t] / after_total, 4),
            "drift": round(drift, 4),
            "action": next((tr["side"] for tr in trades if tr["ticker"] == t), "hold"),
        })
    return {
        **base, "legs": legs_out, "trades": trades, "ignored": ignored,
        "turnover_gbp": round(sum(tr["notional_gbp"] for tr in trades), 2),
        "est_fees_gbp": round(sum(tr["fee_gbp"] for tr in trades), 2),
        "cash_used_gbp": round(max(0.0, sum(tr["notional_gbp"] for tr in trades if tr["side"] == "buy")
                                   - sum(tr["notional_gbp"] for tr in trades if tr["side"] == "sell")), 2),
        "warnings": warnings,
    }


def _acct(kind: str) -> str:
    return {"stocks_isa": "the Stocks ISA", "invest": "Invest", "crypto": "the crypto account"}.get(kind, kind)


def _rationale(play: Play, tr: dict[str, Any], leg: dict[str, Any] | None) -> str:
    if not leg:
        return f"[rebalance] {play.title}: bring {tr['ticker']} back to target."
    return (f"[rebalance] {play.title}: {tr['ticker']} {leg['current_weight']:.0%} → {leg['target_weight']:.0%} of the play "
            f"({'trim' if tr['side'] == 'sell' else 'top up'}).")


def propose(db: Session, play: Play, *, use_cash: bool = False, **plan_kwargs: Any) -> dict[str, Any]:
    """Record the plan's trades as ``proposed`` intents (sells first) with the
    rebalancer's source. Each goes through ``propose_trade``'s rails; a human
    approves. Recomputed from the saved targets — no client-supplied trades."""
    from app.services import proposal_service

    if play.status not in {"active", "paused"}:
        raise RebalanceError("Only an active or paused play can be rebalanced.")
    p = plan(db, play, use_cash=use_cash, **plan_kwargs)
    legs = {l["ticker"]: l for l in p["legs"]}
    created, failed = [], []
    short: set[str] = set()
    for tr in p["trades"]:
        if tr["side"] == "buy" and tr["account_kind"] in short:
            failed.append({"ticker": tr["ticker"], "side": "buy",
                           "reason": "not proposed: the sale funding it was held back"})
            continue
        res = proposal_service.propose_trade(
            db, side=tr["side"], account_kind=tr["account_kind"], rationale=_rationale(play, tr, legs.get(tr["ticker"])),
            instrument_code=tr.get("instrument_code") or None,
            symbol=None if tr.get("instrument_code") else tr["ticker"],
            quantity=tr["shares"] if tr["sell_all"] and tr.get("shares") else None,
            notional_gbp=None if tr["sell_all"] and tr.get("shares") else tr["notional_gbp"],
            play_id=play.id, category="rebalance", rebalancer=True,
        )
        if res.get("ok"):
            created.append({"intent_id": res["intent"]["id"], "ticker": tr["ticker"], "side": tr["side"],
                            "account_kind": tr["account_kind"], "notional_gbp": tr["notional_gbp"],
                            "warnings": res.get("warnings") or []})
        else:
            failed.append({"ticker": tr["ticker"], "side": tr["side"],
                           "reason": "; ".join(res.get("violations") or []) or res.get("error") or "not proposed"})
            if tr["side"] == "sell":
                short.add(tr["account_kind"])
    return {"plan": p, "proposed": created, "failed": failed}


def place(db: Session, play: Play, *, live_confirmed: bool = False, use_cash: bool = False,
          **plan_kwargs: Any) -> dict[str, Any]:
    """The user places the plan's orders by hand (sells first, then buys), each
    through ``manual_trade.place`` — limits advisory, every hard check kept.
    A failed sell skips the buys it was funding in that account."""
    from app.services import manual_trade

    if play.status not in {"active", "paused"}:
        raise RebalanceError("Only an active or paused play can be rebalanced.")
    if manual_trade.broker_mode(db) == "live" and not live_confirmed:
        raise manual_trade.ManualTradeError("confirm the live orders first", code="needs_live_confirmation")
    p = plan(db, play, use_cash=use_cash, **plan_kwargs)
    placed, failed = [], []
    short: set[str] = set()
    for tr in p["trades"]:
        if tr["side"] == "buy" and tr["account_kind"] in short:
            failed.append({"ticker": tr["ticker"], "side": "buy", "reason": "skipped: the sale funding it didn't go through"})
            continue
        ident = {"instrument_code": tr.get("instrument_code"), "display_ticker": tr["ticker"], "ticker": tr["ticker"],
                 "sym": tr["ticker"]}
        try:
            res = manual_trade.place(
                db, ident, side=tr["side"], account_kind=tr["account_kind"], notional_gbp=tr["notional_gbp"],
                live_confirmed=live_confirmed, note=f"Rebalance of “{play.title}”, placed by hand.",
            )
            placed.append({"ticker": tr["ticker"], "side": tr["side"], "account_kind": tr["account_kind"],
                           "notional_gbp": tr["notional_gbp"], "intent_id": res["intent"]["id"],
                           "rail_warnings": res.get("rail_warnings") or []})
        except Exception as exc:  # noqa: BLE001 — report per order, keep going
            failed.append({"ticker": tr["ticker"], "side": tr["side"], "reason": str(exc)})
            if tr["side"] == "sell":
                short.add(tr["account_kind"])
    return {"plan": p, "placed": placed, "failed": failed, "broker_mode": manual_trade.broker_mode(db)}
