"""The built-in "Starter crypto" play: a small, staged, rules-bound way in.

Built for someone who wants some crypto but is nervous about it:

* **small** — sized as 1–3% of the book from conviction, never above the
  crypto cap (default 5%), counting any crypto already held;
* **staged** — equal weekly buys (DCA) over 4–16 weeks (default 10), so no
  single day's price decides the entry;
* **rules** — pause-and-rethink kill rules (sleeve drawdown, cap breach, and
  a plain "if it keeps you up at night, it's too big");
* **explained** — the thesis spells out volatility, fees, custody and UK tax in
  plain English.

Each weekly tranche is an ORDINARY play tranche: ``plays_service.stage_tranche``
turns it into ``proposed`` crypto intents via ``propose_trade``, which a human
approves and ``execute_intent`` places. Nothing here trades.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Play
from app.services.crypto_exchanges import CRYPTO_ACCOUNT, EXCHANGES, SANDBOX_EXCHANGE, market_symbol, yfinance_symbol

ORIGIN = "template:starter_crypto"
CONVICTION_PCT = {"low": 0.01, "medium": 0.02, "high": 0.03}
DEFAULT_WEEKS = 10
WEEKS_BOUNDS = (4, 16)
MIN_TRANCHE_GBP = 10.0  # below this, weekly buys hit exchange minimums / fee floors
STRESS_DRAWDOWN = 0.75  # Bitcoin has fallen ~75%+ from a peak more than once
SLEEVE_DRAWDOWN_KILL = 40


def _fmt(v: float) -> str:
    return f"£{v:,.0f}" if abs(v) >= 100 else f"£{v:,.2f}"


def _exchange(db: Session) -> dict[str, Any]:
    from app.services import crypto_service
    from app.services.config_store import ConfigStore

    cfg = crypto_service.get_config(ConfigStore(db))
    return EXCHANGES.get(cfg.get("exchange") or "") or EXCHANGES[SANDBOX_EXCHANGE]


def template_legs(quote: str, include_eth: bool) -> list[dict[str, Any]]:
    legs = [{"ticker": yfinance_symbol("BTC", quote), "name": "Bitcoin", "role": "core",
             "target_weight": 0.8 if include_eth else 1.0, "instrument_code": market_symbol("BTC", quote),
             "instrument_verified": True,
             "rationale": "The largest, most liquid cryptoasset; the usual first holding."}]
    if include_eth:
        legs.append({"ticker": yfinance_symbol("ETH", quote), "name": "Ether", "role": "satellite", "target_weight": 0.2,
                     "instrument_code": market_symbol("ETH", quote), "instrument_verified": True,
                     "rationale": "Second-largest; more volatile than Bitcoin, so it gets the smaller slice."})
    return legs


def kill_criteria(cap: float) -> list[dict[str, Any]]:
    return [
        {"text": f"The crypto sleeve falls {SLEEVE_DRAWDOWN_KILL}% from its peak: pause the remaining buys and decide again "
                 "(this rule stops new money, it isn't a signal to panic-sell).",
         "rule": {"type": "drawdown_pct", "value": SLEEVE_DRAWDOWN_KILL}},
        {"text": f"Crypto grows past {cap:.0%} of the whole book: stop adding and trim back to the cap.",
         "rule": {"type": "weight_above", "value": cap}},
        {"text": "A 50% drop would make you sell in a panic or lose sleep: the position is too big, so shrink it.",
         "rule": None},
    ]


def thesis(ex: dict[str, Any], weeks: int, include_eth: bool) -> str:
    taker = ex["fees"]["taker"]
    coins = "Bitcoin (80%) and Ether (20%)" if include_eth else "Bitcoin"
    return (
        f"**A small, staged first position in {coins}, bought on {ex['product']} in equal weekly amounts over {weeks} weeks.**\n\n"
        "**Why this shape.** Nobody knows where crypto prices go next. Buying the same amount every week "
        "(dollar-cost averaging) means no single day's price decides your entry, and the position stays small "
        "enough that a crash hurts but doesn't change your life.\n\n"
        "**How volatile it is.** Bitcoin's price typically swings 40–80% a year — several times a stock index — "
        "and it has fallen more than 70% from a peak more than once (2014, 2018, 2022). Ether has been more "
        "volatile still. Plan on seeing this position halve at some point.\n\n"
        f"**What it costs.** {ex['label']} charges about {taker:.2%} per market buy at its entry tier "
        f"({ex['fee_note']}), plus a small spread (usually 0.05–0.2% on GBP pairs). Each proposal shows the "
        "estimated fee and slippage before you approve it. Selling costs the same again.\n\n"
        "**Where it's held.** Coins on an exchange are **not FSCS-protected**: if the exchange failed or was "
        "hacked you might not get them back. The key OpenPF uses is trade-only — it can't withdraw.\n\n"
        "**UK tax.** Selling, swapping or spending crypto is a disposal for Capital Gains Tax; gains above the "
        "annual exempt amount are taxable and you need to keep records of every buy and sell.\n\n"
        "**Every buy needs your approval.** Each weekly tranche arrives as a proposal; nothing is bought until you approve it."
    )


def template_sizing(play: Play, sizing: dict[str, Any], *, book: dict[str, Any], risk: dict[str, Any],
                    override_gbp: float | None = None) -> dict[str, Any]:
    """Replace the generic play sizing with the starter-crypto plan: % of book
    from conviction, capped by the crypto cap; equal weekly DCA tranches."""
    from app.services.crypto_service import max_crypto_pct

    cap = max_crypto_pct(risk)
    book_gbp = float(book.get("total_gbp") or 0.0)
    positions = book.get("positions") or []
    crypto_held = sum(float(p.get("value") or 0) for p in positions if p.get("account_kind") == CRYPTO_ACCOUNT)
    held_in_legs = sum(float(a.get("held_gbp") or 0) for a in sizing.get("allocations") or [])
    base_pct = CONVICTION_PCT.get(play.conviction, CONVICTION_PCT["low"])
    target_pct = min(base_pct, cap)
    target = book_gbp * target_pct
    room = max(cap * book_gbp - crypto_held, 0.0)
    suggested = round(min(max(target - held_in_legs, 0.0), room), 2)
    steps = [f"{play.conviction.capitalize()} conviction → {base_pct:.0%} of your {_fmt(book_gbp)} book "
             f"({_fmt(book_gbp * base_pct)}), never above your {cap:.0%} crypto cap."]
    warnings: list[str] = []
    if crypto_held > 0:
        steps.append(f"You already hold {_fmt(crypto_held)} of crypto, leaving {_fmt(room)} of room under the cap.")
    if override_gbp:
        budget = float(override_gbp)
        steps.append(f"You set the budget at {_fmt(budget)} (the plan suggested {_fmt(suggested)}).")
        if budget > room + 0.01:
            warnings.append(f"That's above the room left under your {cap:.0%} crypto cap ({_fmt(room)}) — "
                            "later buys will be blocked by the crypto rail.")
    else:
        budget = suggested

    weeks = int((play.meta or {}).get("dca_weeks") or DEFAULT_WEEKS)
    weeks = max(WEEKS_BOUNDS[0], min(WEEKS_BOUNDS[1], weeks))
    n = weeks
    while n > WEEKS_BOUNDS[0] and budget / n < MIN_TRANCHE_GBP:
        n -= 1
    if n < weeks:
        steps.append(f"Spread over {n} weeks rather than {weeks} so each buy is at least {_fmt(MIN_TRANCHE_GBP)}.")
    per = budget / n if n else 0.0
    pcts = [round(1 / n, 4)] * n
    pcts[-1] = round(1 - sum(pcts[:-1]), 4)
    tranches = [{"label": "Week 1" if i == 0 else f"Week {i + 1}", "pct_of_budget": p,
                 "trigger": "Now — on activation" if i == 0 else f"Week {i} after activation — same amount, whatever the price"}
                for i, p in enumerate(pcts)]
    legs = play.legs or []
    wsum = sum(float(l.get("target_weight") or 0) for l in legs) or 1.0
    allocations = [{"ticker": l["ticker"], "target_gbp": round(budget * float(l.get("target_weight") or 0) / wsum, 2),
                    "held_gbp": 0.0, "new_gbp": round(budget * float(l.get("target_weight") or 0) / wsum, 2),
                    "weight": float(l.get("target_weight") or 0) / wsum,
                    "pct_of_budget": round(float(l.get("target_weight") or 0) / wsum, 4)} for l in legs]
    small = [a["ticker"] for a in allocations if 0 < per * a["pct_of_budget"] < 5.0]
    if small:
        warnings.append(f"Each weekly {', '.join(small)} buy is under £5 — likely below the exchange minimum; "
                        "consider Bitcoin only or a larger budget.")
    ex = EXCHANGES.get(str((play.meta or {}).get("exchange") or "")) or EXCHANGES[SANDBOX_EXCHANGE]
    fee = per * ex["fees"]["taker"]
    steps.append(f"Bought in {n} equal weekly buys of about {_fmt(per)} — roughly {_fmt(fee)} in {ex['label']} fees each.")
    max_loss = round(budget * STRESS_DRAWDOWN, 2)
    if book_gbp:
        steps.append(f"If crypto fell {STRESS_DRAWDOWN:.0%} (it has before), this plan would lose about {_fmt(max_loss)} "
                     f"— {max_loss / book_gbp:.1%} of your book.")
    single = float(risk.get("max_single_order_notional") or 0)
    if single and per > single:
        warnings.append(f"A weekly buy ({_fmt(per)}) is above your {_fmt(single)} single-order rail.")
    free = float((book.get("free_cash_gbp") or {}).get(CRYPTO_ACCOUNT, 0.0))
    if per and free < per:
        warnings.append(f"Your {ex['label']} account has {_fmt(free)} free — deposit at least {_fmt(per)} "
                        "before approving the first buy.")
    return {
        **sizing,
        "budget_gbp": round(max(budget, 0.0), 2),
        "budget_pct_of_book": round(budget / book_gbp, 4) if book_gbp else 0.0,
        "suggested_gbp": suggested,
        "target_pct_of_book": round(target_pct, 4),
        "base_pct": base_pct,
        "vol_factor": 1.0,
        "max_play_pct": cap,
        "max_loss_gbp": max_loss,
        "stress_drawdown": STRESS_DRAWDOWN,
        "tranches": tranches,
        "allocations": allocations,
        "steps": steps,
        "rationale": " ".join(steps),
        "warnings": warnings,
        "dca": {"weeks": n, "per_tranche_gbp": round(per, 2), "fee_per_tranche_gbp": round(fee, 2),
                "exchange": ex["label"], "fee_rate": ex["fees"]["taker"]},
    }


def create_starter_play(db: Session, *, conviction: str = "low", include_eth: bool = True, weeks: int = DEFAULT_WEEKS,
                        budget_gbp: float | None = None) -> Play:
    """Create the Starter crypto play as a DRAFT. Staging/approving is separate."""
    from app.services import plays_service
    from app.services.config_store import ConfigStore
    from app.services.crypto_service import max_crypto_pct

    if conviction not in CONVICTION_PCT:
        raise plays_service.PlayInputError("Conviction must be low, medium or high.")
    try:
        weeks = int(weeks or DEFAULT_WEEKS)
    except (TypeError, ValueError):
        raise plays_service.PlayInputError("Weeks must be a whole number.") from None
    if not WEEKS_BOUNDS[0] <= weeks <= WEEKS_BOUNDS[1]:
        raise plays_service.PlayInputError(f"Stage it over {WEEKS_BOUNDS[0]}–{WEEKS_BOUNDS[1]} weeks.")
    if budget_gbp is not None and (not math.isfinite(float(budget_gbp)) or float(budget_gbp) < 0):
        raise plays_service.PlayInputError("Budget must be a positive amount in £.")
    ex = _exchange(db)
    cap = max_crypto_pct(ConfigStore(db).get_risk())
    data = {
        "title": "Starter crypto: Bitcoin + Ether" if include_eth else "Starter crypto: Bitcoin",
        "thesis": thesis(ex, weeks, include_eth),
        "conviction": conviction,
        "horizon_months": 24,
        "account_kind": CRYPTO_ACCOUNT,
        "legs": template_legs(ex["quote"], include_eth),
        "kill_criteria": kill_criteria(cap),
        "what_would_change_my_mind": "If I find I'm checking the price every day or can't stomach the swings, "
                                     "the right move is a smaller position — or none.",
        "origin": ORIGIN,
    }
    if budget_gbp:
        data["budget_gbp"] = float(budget_gbp)
    play = plays_service.create_play(db, data)
    play.meta = {**(play.meta or {}), "template": "starter_crypto", "dca_weeks": weeks, "exchange": ex["id"],
                 "include_eth": include_eth}
    try:
        plays_service.resize_play(db, play)
    except Exception:  # noqa: BLE001 — sizing needs a portfolio snapshot; the draft still saves
        pass
    db.add(play)
    db.commit()
    db.refresh(play)
    return play
