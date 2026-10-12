"""Plays — theme bets turned into sized, staged, monitored baskets.

A Play starts as a plain-English idea ("nuclear renaissance"). A dedicated
Claude Agent SDK session researches it into a basket (legs + weights), kill
criteria and a "what would change my mind" line. Everything numeric after that
is deterministic and explainable:

* **Sizing** — % of book from conviction, scaled by basket volatility, net of
  what the user already holds in the candidate tickers, capped by a per-play
  ceiling (10% of book by default) and free cash. Entry is staged in tranches
  sized so a tranche fits inside the daily notional rail.
* **Staging** — a tranche becomes ``proposed`` TradeIntents (never executed
  here); the user approves them like any other proposal.
* **Monitoring** — a cheap daily check evaluates machine-checkable kill rules
  and weight drift and raises Attention alerts when something trips.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import sys
import threading
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Alert, Play, TradeIntent
from app.schemas.plays import PlayDraftOut, PlayReviewOut

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Sizing policy ────────────────────────────────────────────────────────────

# Share of the book a play gets at each conviction level, before adjustments.
CONVICTION_PCT = {"low": 0.02, "medium": 0.04, "high": 0.06}
# Default ceiling for any single play (the user can override with an explicit budget).
MAX_PLAY_PCT = 0.10
# Basket volatility the conviction table is calibrated for (annualised).
REFERENCE_VOL = 0.25
VOL_FACTOR_BOUNDS = (0.5, 1.25)
# Planning assumption for "max loss": a thematic basket can plausibly halve.
STRESS_DRAWDOWN = 0.40
DEFAULT_TRANCHES = 3
MAX_TRANCHES = 6
MIN_LEG_NOTIONAL_GBP = 1.0

VERDICTS = ("on_track", "watch", "broken")
_PLAY_STATUSES = {"draft", "active", "paused", "closed"}


def _now() -> datetime:
    return datetime.utcnow()


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _fmt_gbp(v: float) -> str:
    return f"£{v:,.0f}"


# ── Serialisation ────────────────────────────────────────────────────────────

def serialize_play(p: Play) -> dict[str, Any]:
    return {
        "id": p.id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
        "activated_at": p.activated_at.isoformat() if p.activated_at else None,
        "title": p.title,
        "thesis": p.thesis,
        "status": p.status,
        "conviction": p.conviction,
        "horizon_months": p.horizon_months,
        "account_kind": p.account_kind,
        "budget_gbp": p.budget_gbp,
        "budget_pct_of_book": p.budget_pct_of_book,
        "legs": p.legs or [],
        "sizing": p.sizing or {},
        "kill_criteria": p.kill_criteria or [],
        "what_would_change_my_mind": p.what_would_change_my_mind,
        "review": p.review or {},
        "source_session_id": p.source_session_id,
        "meta": p.meta or {},
    }


# ── Book context (the user's real portfolio) ─────────────────────────────────

def book_context(db: Session) -> dict[str, Any]:
    """Total equity, free cash per account and current holdings, all in GBP."""
    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db, account_kind="all", display_currency="GBP")
    total = float(snap.get("metrics", {}).get("total_value") or snap.get("account", {}).get("total") or 0.0)
    free_cash = {a["account_kind"]: float(a.get("free_cash") or 0.0) for a in snap.get("accounts", [])}
    positions = []
    for p in snap.get("positions", []):
        positions.append({
            "ticker": (p.get("display_ticker") or p.get("ticker") or "").upper(),
            "raw_ticker": (p.get("ticker") or "").upper(),
            "instrument_code": p.get("instrument_code"),
            "yfinance_ticker": p.get("yfinance_ticker"),
            "name": p.get("name"),
            "account_kind": p.get("account_kind"),
            "value": float(p.get("value") or 0.0),
            "ppl": float(p.get("ppl") or 0.0),
            "weight": float(p.get("weight") or 0.0),
        })
    positions.sort(key=lambda r: r["value"], reverse=True)
    return {"total_gbp": total, "free_cash_gbp": free_cash, "positions": positions}


def _leg_keys(leg: dict[str, Any]) -> set[str]:
    t = str(leg.get("ticker") or "").upper()
    keys = {t, t.split(".")[0]}
    code = str(leg.get("instrument_code") or "").upper()
    if code:
        keys.add(code)
        keys.add(code.split("_")[0])
    return {k for k in keys if k}


def _position_matches(pos: dict[str, Any], keys: set[str]) -> bool:
    cands = {
        str(pos.get("ticker") or "").upper(),
        str(pos.get("raw_ticker") or "").upper(),
        str(pos.get("instrument_code") or "").upper(),
        str(pos.get("yfinance_ticker") or "").upper(),
        str(pos.get("yfinance_ticker") or "").upper().split(".")[0],
    }
    return bool(cands & keys)


def overlap_with_book(legs: list[dict[str, Any]], positions: list[dict[str, Any]], account_kind: str | None = None) -> list[dict[str, Any]]:
    """Existing holdings in the play's candidate tickers (any account unless filtered)."""
    out = []
    for leg in legs:
        keys = _leg_keys(leg)
        held = [p for p in positions if _position_matches(p, keys) and (account_kind is None or p.get("account_kind") == account_kind)]
        if held:
            out.append({
                "ticker": leg.get("ticker"),
                "value_gbp": round(sum(p["value"] for p in held), 2),
                "weight_of_book": round(sum(p["weight"] for p in held), 4),
                "accounts": sorted({p["account_kind"] for p in held if p.get("account_kind")}),
            })
    return out


# ── Prices / history (injectable for tests) ──────────────────────────────────

def listing_currency(ticker: str, hint: str | None = None) -> str:
    t = (ticker or "").upper()
    if t.endswith(".L"):
        return "GBX"
    if t.endswith((".AS", ".PA", ".DE", ".MI")):
        return "EUR"
    return (hint or "USD").upper()


def _history_closes(ticker: str, lookback_days: int) -> list[tuple[date, float]]:
    from app.services.market_data import fetch_history

    frame = fetch_history(ticker, lookback_days=lookback_days)
    return [
        (d.date() if isinstance(d, datetime) else d, float(c))
        for d, c in zip(frame["date"], frame["close"])
        if c == c  # drop NaN closes
    ]


def last_price(ticker: str) -> float | None:
    try:
        closes = _history_closes(ticker, 10)
        return closes[-1][1] if closes else None
    except Exception as exc:  # noqa: BLE001
        logger.info("price lookup failed for %s: %s", ticker, exc)
        return None


def gbp_to(currency: str) -> float:
    """Units of ``currency`` per 1 GBP."""
    from app.services.fx import get_fx_rate

    if currency.upper() in {"GBP"}:
        return 1.0
    return float(get_fx_rate("GBP", currency))


# ── Sizing (pure) ────────────────────────────────────────────────────────────

def basket_volatility(legs: list[dict[str, Any]], history_fn: Callable[[str, int], list[tuple[date, float]]] = _history_closes) -> float | None:
    """Annualised volatility of the weighted basket's daily returns (last ~6 months)."""
    series: dict[str, dict[date, float]] = {}
    for leg in legs:
        try:
            closes = history_fn(leg["ticker"], 200)
        except Exception:  # noqa: BLE001
            continue
        rets = {}
        for (_, p0), (d1, p1) in zip(closes, closes[1:]):
            if p0 > 0:
                rets[d1] = p1 / p0 - 1
        if len(rets) > 20:
            series[leg["ticker"]] = rets
    if not series:
        return None
    weights = {leg["ticker"]: float(leg.get("target_weight") or 0.0) for leg in legs if leg["ticker"] in series}
    wsum = sum(weights.values()) or 1.0
    common = set.intersection(*(set(s) for s in series.values()))
    if len(common) < 20:
        return None
    basket = [sum(series[t][d] * weights[t] / wsum for t in series) for d in sorted(common)]
    mean = sum(basket) / len(basket)
    var = sum((r - mean) ** 2 for r in basket) / (len(basket) - 1)
    return math.sqrt(var) * math.sqrt(252)


def compute_sizing(
    *,
    book_gbp: float,
    conviction: str,
    basket_vol: float | None,
    overlap_gbp: float,
    free_cash_gbp: float,
    risk: dict[str, Any],
    budget_override_gbp: float | None = None,
    max_play_pct: float = MAX_PLAY_PCT,
    horizon_months: int = 12,
    max_leg_weight: float | None = None,
) -> dict[str, Any]:
    """Deterministic, explainable play size + staged entry.

    target exposure = book × conviction% × vol factor, capped at ``max_play_pct``;
    new money = target − what's already held in these names; then capped by the
    account's free cash. A user-supplied budget wins but is flagged when above the cap.
    """
    conviction = conviction if conviction in CONVICTION_PCT else "medium"
    base_pct = CONVICTION_PCT[conviction]
    vol_factor = 1.0 if not basket_vol else _clamp(REFERENCE_VOL / basket_vol, *VOL_FACTOR_BOUNDS)
    target_pct = min(base_pct * vol_factor, max_play_pct)
    target_gbp = book_gbp * target_pct
    cap_gbp = book_gbp * max_play_pct
    steps: list[str] = []
    warnings: list[str] = []

    steps.append(f"{conviction.capitalize()} conviction → {base_pct:.0%} of your {_fmt_gbp(book_gbp)} book.")
    if basket_vol:
        direction = "trimmed" if vol_factor < 1 else ("raised" if vol_factor > 1 else "kept")
        steps.append(
            f"Basket volatility is {basket_vol:.0%}/yr vs a {REFERENCE_VOL:.0%} reference, so the size is "
            f"{direction} ×{vol_factor:.2f} → {target_pct:.1%} ({_fmt_gbp(target_gbp)})."
        )
    else:
        steps.append("Basket volatility unavailable — no volatility adjustment applied.")

    suggested = max(target_gbp - overlap_gbp, 0.0)
    if overlap_gbp > 0:
        steps.append(f"You already hold {_fmt_gbp(overlap_gbp)} in these names, so new money is {_fmt_gbp(suggested)}.")

    if budget_override_gbp is not None and budget_override_gbp > 0:
        budget = float(budget_override_gbp)
        steps.append(f"You set the budget at {_fmt_gbp(budget)} (Archie suggested {_fmt_gbp(suggested)}).")
        if budget + overlap_gbp > cap_gbp:
            warnings.append(
                f"Budget plus existing exposure is {(budget + overlap_gbp) / book_gbp:.1%} of book — above the {max_play_pct:.0%} per-play ceiling."
            )
    else:
        budget = suggested

    if free_cash_gbp >= 0 and budget > free_cash_gbp:
        if budget_override_gbp:
            warnings.append(f"Only {_fmt_gbp(free_cash_gbp)} free cash in this account — later tranches need funding.")
        else:
            steps.append(f"Capped at the account's free cash ({_fmt_gbp(free_cash_gbp)}).")
            budget = free_cash_gbp

    budget = round(max(budget, 0.0), 2)
    pct_of_book = (budget / book_gbp) if book_gbp else 0.0

    tranches = build_tranches(budget, risk, horizon_months=horizon_months, max_leg_weight=max_leg_weight)
    n = len(tranches)
    daily = float(risk.get("max_daily_notional") or 0.0)
    if n > DEFAULT_TRANCHES:
        steps.append(f"Staged in {n} tranches so each order fits your risk rails "
                     f"({_fmt_gbp(float(risk.get('max_single_order_notional') or 0))} per order, {_fmt_gbp(daily)} per day).")
    else:
        steps.append(f"Staged in {n} tranches to average in rather than buy the top.")
    if daily and tranches and max(t["pct_of_budget"] for t in tranches) * budget > daily:
        warnings.append(f"Each tranche still exceeds the {_fmt_gbp(daily)} daily rail — stage over several days.")

    max_loss = round(budget * STRESS_DRAWDOWN, 2)
    steps.append(f"A {STRESS_DRAWDOWN:.0%} drawdown on the full position would cost ~{_fmt_gbp(max_loss)} ({max_loss / book_gbp:.1%} of book)." if book_gbp else "")

    return {
        "budget_gbp": budget,
        "budget_pct_of_book": round(pct_of_book, 4),
        "suggested_gbp": round(suggested, 2),
        "target_pct_of_book": round(target_pct, 4),
        "base_pct": base_pct,
        "vol_factor": round(vol_factor, 3),
        "basket_vol": round(basket_vol, 4) if basket_vol else None,
        "overlap_gbp": round(overlap_gbp, 2),
        "max_play_pct": max_play_pct,
        "max_loss_gbp": max_loss,
        "tranches": tranches,
        "rationale": " ".join(s for s in steps if s),
        "steps": [s for s in steps if s],
        "warnings": warnings,
    }


def build_tranches(budget_gbp: float, risk: dict[str, Any], *, horizon_months: int = 12,
                   max_leg_weight: float | None = None) -> list[dict[str, Any]]:
    """Staged entry: 40/30/30 by default; more (equal) tranches when a tranche
    would not fit inside the daily notional rail, or its largest leg would not
    fit inside the single-order rail."""
    daily = float(risk.get("max_daily_notional") or 0.0)
    single = float(risk.get("max_single_order_notional") or 0.0)
    leg_w = float(max_leg_weight or 0.0)

    def _pcts(n: int) -> list[float]:
        if n == DEFAULT_TRANCHES:
            return [0.4, 0.3, 0.3]
        p = [round(1 / n, 4)] * n
        p[-1] = round(1 - sum(p[:-1]), 4)
        return p

    def _fits(pcts: list[float]) -> bool:
        top = max(pcts) * budget_gbp
        return (not daily or top <= daily + 1e-6) and (not single or not leg_w or top * leg_w <= single + 1e-6)

    n = DEFAULT_TRANCHES
    while n < MAX_TRANCHES and budget_gbp > 0 and not _fits(_pcts(n)):
        n += 1
    pcts = _pcts(n)
    spacing_weeks = max(2, min(6, round(horizon_months * 4.3 / 8)))
    out = []
    for i, pct in enumerate(pcts):
        if i == 0:
            label, trigger = "Starter", "Now — on activation"
        elif i == len(pcts) - 1:
            label, trigger = "Final", f"Week {spacing_weeks * i}, if the thesis is intact and no kill rule is near"
        else:
            label, trigger = f"Add {i}", f"Week {spacing_weeks * i}, or earlier on a 10% pullback in the basket"
        out.append({"label": label, "pct_of_budget": pct, "trigger": trigger})
    return out


# ── Kill-rule evaluation (pure) ──────────────────────────────────────────────

def evaluate_rule(rule: dict[str, Any] | None, ctx: dict[str, Any]) -> dict[str, Any]:
    """Return ``{state: intact|near|tripped|unknown, detail}`` for one rule.

    ctx keys: ``prices`` {ticker: price}, ``drawdown_pct`` (basket drawdown from
    its peak since activation, as a positive %), ``weights_of_book``
    {ticker|'__play__': fraction}, ``today`` (date).
    """
    if not rule:
        return {"state": "manual", "detail": "Judgement call — checked in reviews"}
    rtype = rule.get("type")
    value = rule.get("value")
    ticker = (rule.get("ticker") or "").upper() or None
    try:
        if rtype == "price_below":
            price = (ctx.get("prices") or {}).get(ticker) if ticker else None
            if price is None:
                return {"state": "unknown", "detail": f"No price for {ticker}"}
            v = float(value)
            if price < v:
                return {"state": "tripped", "detail": f"{ticker} {price:,.2f} < {v:,.2f}"}
            if price < v * 1.05:
                return {"state": "near", "detail": f"{ticker} {price:,.2f} within 5% of {v:,.2f}"}
            return {"state": "intact", "detail": f"{ticker} {price:,.2f} vs {v:,.2f}"}
        if rtype == "drawdown_pct":
            dd = ctx.get("drawdown_pct")
            if dd is None:
                return {"state": "unknown", "detail": "No basket history yet"}
            v = abs(float(value))
            if dd >= v:
                return {"state": "tripped", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
            if dd >= 0.75 * v:
                return {"state": "near", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
            return {"state": "intact", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
        if rtype == "weight_above":
            key = ticker or "__play__"
            w = (ctx.get("weights_of_book") or {}).get(key)
            if w is None:
                return {"state": "unknown", "detail": "Not held yet"}
            v = float(value)
            v = v / 100 if v > 1 else v
            label = ticker or "Play"
            if w > v:
                return {"state": "tripped", "detail": f"{label} is {w:.1%} of book (max {v:.0%})"}
            if w > 0.9 * v:
                return {"state": "near", "detail": f"{label} is {w:.1%} of book (max {v:.0%})"}
            return {"state": "intact", "detail": f"{label} is {w:.1%} of book (max {v:.0%})"}
        if rtype == "date_after":
            deadline = date.fromisoformat(str(value)[:10])
            today = ctx.get("today") or date.today()
            if today > deadline:
                return {"state": "tripped", "detail": f"Deadline {deadline.isoformat()} passed"}
            if (deadline - today).days <= 14:
                return {"state": "near", "detail": f"Deadline {deadline.isoformat()} in {(deadline - today).days}d"}
            return {"state": "intact", "detail": f"Deadline {deadline.isoformat()}"}
    except (TypeError, ValueError) as exc:
        return {"state": "unknown", "detail": f"Invalid rule: {exc}"}
    return {"state": "unknown", "detail": f"Unknown rule type {rtype}"}


def verdict_from_states(states: list[str], drift: bool = False) -> str:
    if "tripped" in states:
        return "broken"
    if "near" in states or drift:
        return "watch"
    return "on_track"


# ── Instruments ──────────────────────────────────────────────────────────────

def resolve_instrument(db: Session, ticker: str) -> str:
    """Best-effort T212 instrument code for a Yahoo-style ticker."""
    t = (ticker or "").strip().upper()
    base = t.split(".")[0]
    is_lse = t.endswith(".L")
    try:
        from app.services.portfolio_service import search_instruments

        rows = search_instruments(base, db, limit=10)
        for r in rows:
            code = str(r.get("instrument_code") or "")
            if r.get("ticker", "").upper() != base and str(r.get("display_ticker") or "").upper() != base:
                continue
            if is_lse and code.lower().endswith("l_eq"):
                return code
            if not is_lse and code.upper().endswith("_US_EQ"):
                return code
    except Exception as exc:  # noqa: BLE001
        logger.debug("instrument search failed for %s: %s", ticker, exc)
    return f"{base}l_EQ" if is_lse else f"{base}_US_EQ"


# ── CRUD ─────────────────────────────────────────────────────────────────────

def list_plays(db: Session, status: str | None = None) -> list[Play]:
    q = select(Play).order_by(Play.created_at.desc())
    if status and status != "all":
        q = q.where(Play.status == status)
    return list(db.execute(q).scalars().all())


def get_play(db: Session, play_id: str) -> Play | None:
    return db.get(Play, play_id)


def _normalise_legs(legs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    total = sum(float(l.get("target_weight") or 0.0) for l in legs) or 1.0
    out = []
    for l in legs:
        d = dict(l)
        d["ticker"] = str(d.get("ticker") or "").strip().upper()
        d["target_weight"] = round(float(d.get("target_weight") or 0.0) / total, 4)
        out.append(d)
    return out


def update_play(db: Session, play: Play, patch: dict[str, Any], *, risk: dict[str, Any] | None = None) -> Play:
    resize = bool(patch.pop("resize", False))
    for key in ("title", "thesis", "conviction", "horizon_months", "account_kind", "what_would_change_my_mind"):
        if patch.get(key) is not None:
            setattr(play, key, patch[key])
    if patch.get("legs") is not None:
        legs = _normalise_legs([dict(l) for l in patch["legs"]])
        for l in legs:
            if not l.get("instrument_code"):
                l["instrument_code"] = resolve_instrument(db, l["ticker"])
        play.legs = legs
    if patch.get("kill_criteria") is not None:
        play.kill_criteria = [dict(c) for c in patch["kill_criteria"]]
    if patch.get("budget_gbp") is not None:
        play.budget_gbp = float(patch["budget_gbp"])
        sizing = dict(play.sizing or {})
        sizing["budget_override"] = True
        play.sizing = sizing
    if patch.get("status") is not None:
        set_status(play, patch["status"])
    if resize:
        resize_play(db, play, risk=risk)
    db.add(play)
    db.commit()
    db.refresh(play)
    return play


def set_status(play: Play, status: str) -> None:
    if status not in _PLAY_STATUSES:
        raise ValueError(f"invalid status {status}")
    if status == "active" and not play.activated_at:
        play.activated_at = _now()
    play.status = status


def resize_play(db: Session, play: Play, *, risk: dict[str, Any] | None = None, book: dict[str, Any] | None = None) -> None:
    """Recompute sizing for a play's current legs/conviction/budget."""
    from app.services.config_store import ConfigStore

    risk = risk or ConfigStore(db).get_risk()
    book = book or book_context(db)
    overlap = overlap_with_book(play.legs or [], book["positions"])
    override = play.budget_gbp if (play.sizing or {}).get("budget_override") else None
    sizing = compute_sizing(
        book_gbp=book["total_gbp"],
        conviction=play.conviction,
        basket_vol=basket_volatility(play.legs or []),
        overlap_gbp=sum(o["value_gbp"] for o in overlap),
        free_cash_gbp=book["free_cash_gbp"].get(play.account_kind, 0.0),
        risk=risk,
        budget_override_gbp=override,
        horizon_months=play.horizon_months,
        max_leg_weight=max((float(l.get("target_weight") or 0) for l in play.legs or []), default=None),
    )
    sizing["budget_override"] = bool(override)
    sizing["staged"] = (play.sizing or {}).get("staged", [])
    play.sizing = sizing
    play.budget_gbp = sizing["budget_gbp"]
    play.budget_pct_of_book = sizing["budget_pct_of_book"]
    meta = dict(play.meta or {})
    meta["overlap"] = overlap
    play.meta = meta


def create_play(db: Session, data: dict[str, Any]) -> Play:
    play = Play(
        title=data.get("title") or "Untitled play",
        thesis=data.get("thesis") or "",
        conviction=data.get("conviction") or "medium",
        horizon_months=int(data.get("horizon_months") or 12),
        account_kind=data.get("account_kind") or "stocks_isa",
        what_would_change_my_mind=data.get("what_would_change_my_mind") or "",
        kill_criteria=[dict(c) for c in data.get("kill_criteria") or []],
        status="draft",
        review={},
        meta={},
    )
    legs = _normalise_legs([dict(l) for l in data.get("legs") or []])
    for l in legs:
        if not l.get("instrument_code"):
            l["instrument_code"] = resolve_instrument(db, l["ticker"])
    play.legs = legs
    if data.get("budget_gbp"):
        play.budget_gbp = float(data["budget_gbp"])
        play.sizing = {"budget_override": True}
    db.add(play)
    db.flush()
    try:
        resize_play(db, play)
    except Exception as exc:  # noqa: BLE001 — sizing needs a portfolio; a manual play can still be saved
        logger.warning("sizing failed for new play: %s", exc)
    db.commit()
    db.refresh(play)
    return play


def delete_play(db: Session, play: Play) -> None:
    db.delete(play)
    db.commit()


# ── Staging (proposals only — never executes) ────────────────────────────────

def _create_intent(db: Session, *, play: Play, leg: dict[str, Any], quantity: float, notional_gbp: float,
                   tranche_index: int, tranche_label: str, broker_mode: str, breaches: list[str]) -> TradeIntent:
    intent = TradeIntent(
        status="proposed",
        broker_mode=broker_mode,
        symbol=leg["ticker"],
        instrument_code=leg.get("instrument_code") or leg["ticker"],
        side="buy",
        order_type="market",
        quantity=quantity,
        estimated_notional=round(notional_gbp, 2),
        expected_edge=0.0,
        confidence={"low": 0.5, "medium": 0.65, "high": 0.8}.get(play.conviction, 0.65),
        risk_score=0.4,
        rationale=(
            f"[play] {play.title} — {tranche_label} tranche: {leg.get('role', 'core')} leg "
            f"({leg.get('target_weight', 0):.0%} of play). {leg.get('rationale', '')}".strip()
        ),
        meta={
            "source": "play",
            "play_id": play.id,
            "play_title": play.title,
            "tranche_index": tranche_index,
            "tranche_label": tranche_label,
            "account_kind": play.account_kind,
            "leg_role": leg.get("role"),
            "notional_currency": "GBP",
            "rail_breaches": breaches,
        },
    )
    db.add(intent)
    db.flush()
    return intent


def _executed_notional_today(db: Session) -> float:
    try:
        from app.services.execution_service import _daily_executed_notional

        return float(_daily_executed_notional(db))
    except Exception:  # noqa: BLE001
        return 0.0


def stage_tranche(
    db: Session,
    play: Play,
    tranche_index: int | None = None,
    *,
    price_fn: Callable[[str], float | None] = last_price,
    fx_fn: Callable[[str], float] = gbp_to,
    risk: dict[str, Any] | None = None,
    free_cash_gbp: float | None = None,
) -> dict[str, Any]:
    """Turn the next (or given) tranche into ``proposed`` buy intents."""
    from app.services.config_store import ConfigStore

    if play.status not in {"active", "draft"}:
        raise ValueError(f"Play is {play.status}; activate it before staging")
    store = ConfigStore(db)
    risk = risk or store.get_risk()
    broker_mode = store.get_broker().get("broker_mode", "paper")
    sizing = dict(play.sizing or {})
    tranches = sizing.get("tranches") or []
    staged = list(sizing.get("staged") or [])
    staged_idx = {s["index"] for s in staged}
    if tranche_index is None:
        tranche_index = next((i for i in range(len(tranches)) if i not in staged_idx), None)
        if tranche_index is None:
            raise ValueError("All tranches are already staged")
    if tranche_index >= len(tranches):
        raise ValueError(f"Tranche {tranche_index} does not exist")
    if tranche_index in staged_idx:
        raise ValueError(f"Tranche {tranche_index + 1} is already staged")

    tranche = tranches[tranche_index]
    tranche_gbp = float(play.budget_gbp) * float(tranche["pct_of_budget"])
    max_single = float(risk.get("max_single_order_notional") or 0.0)
    max_daily = float(risk.get("max_daily_notional") or 0.0)
    daily_used = _executed_notional_today(db)

    if free_cash_gbp is None:
        try:
            free_cash_gbp = book_context(db)["free_cash_gbp"].get(play.account_kind)
        except Exception:  # noqa: BLE001
            free_cash_gbp = None

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    breaches_all: list[dict[str, Any]] = []
    running = 0.0
    for leg in play.legs or []:
        notional = tranche_gbp * float(leg.get("target_weight") or 0.0)
        if notional < MIN_LEG_NOTIONAL_GBP:
            skipped.append({"ticker": leg["ticker"], "reason": "below minimum order size"})
            continue
        price = price_fn(leg["ticker"])
        if not price or price <= 0:
            skipped.append({"ticker": leg["ticker"], "reason": "no live price"})
            continue
        ccy = listing_currency(leg["ticker"], leg.get("currency"))
        quantity = round(notional * fx_fn(ccy) / price, 4)
        if quantity <= 0:
            skipped.append({"ticker": leg["ticker"], "reason": "quantity rounds to zero"})
            continue
        running += notional
        breaches: list[str] = []
        if max_single and notional > max_single:
            breaches.append(f"exceeds max single order ({_fmt_gbp(max_single)})")
        if max_daily and daily_used + running > max_daily:
            breaches.append(f"pushes today's notional past {_fmt_gbp(max_daily)}")
        intent = _create_intent(
            db, play=play, leg=leg, quantity=quantity, notional_gbp=notional,
            tranche_index=tranche_index, tranche_label=tranche["label"], broker_mode=broker_mode, breaches=breaches,
        )
        row = {"intent_id": intent.id, "ticker": leg["ticker"], "quantity": quantity,
               "notional_gbp": round(notional, 2), "price": price, "currency": ccy, "breaches": breaches}
        created.append(row)
        if breaches:
            breaches_all.append({"ticker": leg["ticker"], "breaches": breaches})

    warnings: list[str] = []
    if free_cash_gbp is not None and running > free_cash_gbp:
        warnings.append(f"Tranche needs {_fmt_gbp(running)} but the account has {_fmt_gbp(free_cash_gbp)} free cash.")

    if created:
        staged.append({"index": tranche_index, "staged_at": _now().isoformat(),
                       "intent_ids": [c["intent_id"] for c in created], "notional_gbp": round(running, 2)})
        sizing["staged"] = staged
        play.sizing = sizing
        if play.status == "draft":
            set_status(play, "active")
        db.add(play)
    db.commit()
    return {
        "play_id": play.id,
        "tranche_index": tranche_index,
        "tranche": tranche,
        "notional_gbp": round(running, 2),
        "intents": created,
        "skipped": skipped,
        "rail_breaches": breaches_all,
        "warnings": warnings,
    }


# ── Performance ──────────────────────────────────────────────────────────────

def _basket_series(legs: list[dict[str, Any]], since: date, benchmark: str,
                   history_fn: Callable[[str, int], list[tuple[date, float]]] = _history_closes) -> dict[str, Any]:
    days = max((date.today() - since).days + 10, 30)
    per: dict[str, dict[date, float]] = {}
    for leg in legs:
        try:
            closes = [(d, p) for d, p in history_fn(leg["ticker"], days) if d >= since]
        except Exception:  # noqa: BLE001
            continue
        if len(closes) >= 2 and closes[0][1] > 0:
            base = closes[0][1]
            per[leg["ticker"]] = {d: p / base for d, p in closes}
    bench: dict[date, float] = {}
    try:
        closes = [(d, p) for d, p in history_fn(benchmark, days) if d >= since]
        if closes and closes[0][1] > 0:
            bench = {d: p / closes[0][1] for d, p in closes}
    except Exception:  # noqa: BLE001
        pass
    if not per:
        return {"points": [], "basket_return": None, "benchmark_return": None, "drawdown_pct": None}
    weights = {l["ticker"]: float(l.get("target_weight") or 0.0) for l in legs if l["ticker"] in per}
    wsum = sum(weights.values()) or 1.0
    dates = sorted(set.intersection(*(set(s) for s in per.values())))
    points = []
    peak = 0.0
    dd = 0.0
    for d in dates:
        idx = sum(per[t][d] * weights[t] / wsum for t in per)
        peak = max(peak, idx)
        dd = max(dd, (1 - idx / peak) * 100 if peak else 0.0)
        points.append({"date": d.isoformat(), "basket": round((idx - 1) * 100, 3),
                       "benchmark": round((bench[d] - 1) * 100, 3) if d in bench else None})
    current_dd = (1 - (points[-1]["basket"] / 100 + 1) / peak) * 100 if points and peak else None
    return {
        "points": points,
        "basket_return": points[-1]["basket"] / 100 if points else None,
        "benchmark_return": (points[-1]["benchmark"] / 100) if points and points[-1]["benchmark"] is not None else None,
        "drawdown_pct": round(current_dd, 2) if current_dd is not None else None,
        "max_drawdown_pct": round(dd, 2),
    }


def play_performance(db: Session, play: Play, *, book: dict[str, Any] | None = None,
                     history_fn: Callable[[str, int], list[tuple[date, float]]] = _history_closes,
                     price_fn: Callable[[str], float | None] = last_price) -> dict[str, Any]:
    book = book or book_context(db)
    legs_out = []
    held_total = 0.0
    for leg in play.legs or []:
        keys = _leg_keys(leg)
        held = [p for p in book["positions"] if _position_matches(p, keys) and p.get("account_kind") == play.account_kind]
        value = sum(p["value"] for p in held)
        ppl = sum(p["ppl"] for p in held)
        held_total += value
        legs_out.append({**leg, "value_gbp": round(value, 2), "ppl_gbp": round(ppl, 2),
                         "weight_of_book": (value / book["total_gbp"]) if book["total_gbp"] else 0.0})
    for l in legs_out:
        l["actual_weight"] = (l["value_gbp"] / held_total) if held_total else 0.0
        l["drift"] = l["actual_weight"] - float(l.get("target_weight") or 0.0) if held_total else 0.0

    since_dt = play.activated_at or play.created_at or _now()
    benchmark = (play.meta or {}).get("benchmark") or "SPY"
    series = _basket_series(play.legs or [], since_dt.date(), benchmark, history_fn) if play.status != "draft" else {
        "points": [], "basket_return": None, "benchmark_return": None, "drawdown_pct": None}

    tranches = (play.sizing or {}).get("tranches") or []
    staged = (play.sizing or {}).get("staged") or []
    prices = {l["ticker"]: price_fn(l["ticker"]) for l in play.legs or [] if any(
        (c.get("rule") or {}).get("type") == "price_below" and (c.get("rule") or {}).get("ticker", "").upper() == l["ticker"]
        for c in play.kill_criteria or [])}
    weights = {l["ticker"]: l["weight_of_book"] for l in legs_out if l["value_gbp"] > 0}
    if held_total > 0 and book["total_gbp"]:
        weights["__play__"] = held_total / book["total_gbp"]
    ctx = {"prices": prices, "drawdown_pct": series.get("drawdown_pct"), "weights_of_book": weights, "today": date.today()}
    kill_status = [{**c, **evaluate_rule(c.get("rule"), ctx)} for c in play.kill_criteria or []]

    return {
        "play_id": play.id,
        "held_gbp": round(held_total, 2),
        "budget_gbp": play.budget_gbp,
        "exposure_pct_of_budget": (held_total / play.budget_gbp) if play.budget_gbp else None,
        "legs": legs_out,
        "benchmark": benchmark,
        "since": since_dt.isoformat(),
        **series,
        "tranches_total": len(tranches),
        "tranches_staged": len(staged),
        "staged": staged,
        "kill_status": kill_status,
    }


# ── Monitoring (deterministic, no LLM) ───────────────────────────────────────

DRIFT_THRESHOLD = 0.15


def _append_review(play: Play, verdict: str, notes: str, source: str) -> None:
    review = dict(play.review or {})
    history = list(review.get("history") or [])
    entry = {"at": _now().isoformat(), "verdict": verdict, "notes": notes, "source": source}
    history.insert(0, entry)
    review.update({"last_reviewed_at": entry["at"], "verdict": verdict, "notes": notes, "history": history[:20]})
    play.review = review


def review_active_plays(db: Session, **perf_kwargs: Any) -> dict[str, Any]:
    """Evaluate kill rules + drift for every active play; raise deduped alerts."""
    plays = list_plays(db, status="active")
    if not plays:
        return {"reviewed": 0, "alerts": 0}
    book = perf_kwargs.pop("book", None) or book_context(db)
    open_keys = set(db.execute(select(Alert.dedupe_key).where(Alert.status.in_(["new", "seen"]))).scalars().all())
    created = 0
    for play in plays:
        try:
            perf = play_performance(db, play, book=book, **perf_kwargs)
        except Exception as exc:  # noqa: BLE001 — one play failing must not sink the rest
            logger.warning("play review failed for %s: %s", play.id, exc)
            continue
        states = [k["state"] for k in perf["kill_status"]]
        drifted = [l for l in perf["legs"] if perf["held_gbp"] > 0 and abs(l["drift"]) > DRIFT_THRESHOLD]
        verdict = verdict_from_states(states, drift=bool(drifted))
        notes_parts = []
        for i, k in enumerate(perf["kill_status"]):
            if k["state"] not in {"tripped", "near"}:
                continue
            notes_parts.append(f"{'Tripped' if k['state'] == 'tripped' else 'Near'}: {k['text']} ({k['detail']})")
            key = f"play:{play.id}:kill:{i}:{k['state']}"
            if k["state"] == "tripped" and key not in open_keys:
                db.add(Alert(
                    category="play", severity="critical" if (k.get("rule") or {}).get("type") in {"drawdown_pct", "price_below"} else "warning",
                    title=f"{play.title}: kill rule tripped",
                    detail=f"{k['text']} — {k['detail']}.",
                    consider="Review the play: close it, pause new tranches, or update the thesis.",
                    ticker=(k.get("rule") or {}).get("ticker"), dedupe_key=key, source="plays",
                    meta={"play_id": play.id, "link": f"/plays/{play.id}"},
                ))
                open_keys.add(key)
                created += 1
        for l in drifted:
            notes_parts.append(f"{l['ticker']} is {l['actual_weight']:.0%} of the play vs {l['target_weight']:.0%} target")
        if drifted:
            key = f"play:{play.id}:drift"
            if key not in open_keys:
                db.add(Alert(
                    category="play", severity="info",
                    title=f"{play.title}: basket has drifted",
                    detail="; ".join(f"{l['ticker']} {l['actual_weight']:.0%} vs {l['target_weight']:.0%} target" for l in drifted) + ".",
                    consider="Stage the next tranche into the underweight legs or rebalance.",
                    dedupe_key=key, source="plays", meta={"play_id": play.id, "link": f"/plays/{play.id}"},
                ))
                open_keys.add(key)
                created += 1
        _append_review(play, verdict, "; ".join(notes_parts) or "All kill rules intact.", "monitor")
        db.add(play)
    db.commit()
    return {"reviewed": len(plays), "alerts": created}


# ── LLM: drafting + narrative review ─────────────────────────────────────────

_MCP_DIR = Path(__file__).resolve().parents[2] / "mcp_servers"
_DRAFT_TOOLS = [
    "mcp__marketdata__get_price_snapshot",
    "mcp__marketdata__get_risk_metrics",
    "mcp__marketdata__compare_assets",
    "mcp__marketdata__get_correlation_matrix",
    "mcp__fundamentals__get_fundamentals",
    "mcp__fundamentals__get_valuation",
    "WebSearch",
]

_DRAFT_SYSTEM = """You are Archie, a careful UK-based portfolio analyst. You turn a user's theme idea into a
"Play": a small, diversified basket of listed instruments that expresses the thesis, plus explicit kill criteria.

Rules:
- The user invests via Trading 212 (UK). In a Stocks ISA, US-domiciled ETFs are NOT available (PRIIPs);
  prefer individual US/UK stocks or London-listed UCITS ETFs (Yahoo symbols ending .L). In the Invest account
  US stocks are fine; still avoid US-domiciled ETFs.
- 3-7 legs. Mix roles: "core" (purest, most liquid exposure), "satellite" (higher-beta / smaller), and
  optionally a "hedge". Weights sum to 1. No leveraged or inverse products.
- Use the tools to sanity-check that each ticker exists, is liquid, and to gauge volatility/valuation. Be brief:
  a handful of tool calls, not dozens.
- Mind the user's existing holdings: avoid doubling up on names they already hold heavily; mention overlap.
- Kill criteria: 2-4 items. At least 2 must have a machine-checkable `rule`:
  price_below {ticker, value in the listing currency}, drawdown_pct {value: % basket drawdown from peak},
  weight_above {ticker or null for whole play, value: fraction of the total book e.g. 0.12},
  date_after {value: ISO date by which the thesis must have shown progress}.
- Do NOT size the position — the app sizes it deterministically against the user's book.
- "what_would_change_my_mind": one or two sentences, concrete and falsifiable.
- Thesis: markdown, 2-4 short paragraphs, plain English, no hype.
Return only the structured JSON."""


class DraftJob:
    def __init__(self, request: dict[str, Any]):
        self.id = uuid.uuid4().hex[:12]
        self.request = request
        self.status = "running"  # running | done | error
        self.progress: list[dict[str, str]] = []
        self.play_id: str | None = None
        self.error: str | None = None
        self.started_at = _now()
        self.finished_at: datetime | None = None
        self.add("Reading your portfolio")

    def add(self, line: str) -> None:
        if self.progress and self.progress[-1]["text"] == line:
            return
        self.progress.append({"at": _now().isoformat(), "text": line})

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id, "status": self.status, "progress": self.progress, "play_id": self.play_id,
            "error": self.error, "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


_JOBS: dict[str, DraftJob] = {}
_JOBS_LOCK = threading.Lock()


def get_draft_job(job_id: str) -> DraftJob | None:
    return _JOBS.get(job_id)


def _mcp_servers() -> dict[str, Any]:
    backend_root = str(_MCP_DIR.parent)
    db_url = settings.database_url
    if db_url.startswith("sqlite:///./"):
        db_url = f"sqlite:///{(Path(backend_root) / db_url.replace('sqlite:///./', '', 1)).resolve()}"
    env = {"PYTHONPATH": backend_root, "DATABASE_URL": db_url}
    servers = {}
    for name in ("marketdata", "fundamentals"):
        script = _MCP_DIR / f"{name}.py"
        if script.is_file():
            servers[name] = {"type": "stdio", "command": sys.executable, "args": [str(script)], "env": env}
    return servers


def _tool_line(name: str, args: dict[str, Any]) -> str:
    target = args.get("ticker") or args.get("tickers") or args.get("query") or ""
    if isinstance(target, list):
        target = ", ".join(map(str, target))
    target = str(target)[:80]
    short = name.split("__")[-1]
    labels = {
        "get_price_snapshot": "Checking price for",
        "get_risk_metrics": "Measuring risk for",
        "compare_assets": "Comparing",
        "get_correlation_matrix": "Checking correlations across",
        "get_fundamentals": "Reading fundamentals for",
        "get_valuation": "Checking valuation for",
        "WebSearch": "Searching the web:",
    }
    return f"{labels.get(short, 'Using ' + short)} {target}".strip()


def _extract_json(text: str) -> dict[str, Any] | None:
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", text)
    for cand in ([m.group(1)] if m else []) + re.findall(r"\{[\s\S]*\}", text):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


async def _run_structured(prompt: str, *, system: str, schema: dict[str, Any], model: str, tools: list[str],
                          mcp: dict[str, Any], max_turns: int, on_progress: Callable[[str], None] | None,
                          usage_source: str, usage_id: str) -> dict[str, Any] | None:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, query
    from claude_agent_sdk.types import ToolUseBlock

    from app.services.claude_sdk_config import configure_sdk_auth, resolve_sdk_cwd

    configure_sdk_auth()
    options = ClaudeAgentOptions(
        system_prompt=system,
        model=model,
        cwd=str(resolve_sdk_cwd()),
        setting_sources=[],
        allowed_tools=tools,
        mcp_servers=mcp,
        max_turns=max_turns,
        permission_mode="bypassPermissions" if tools else None,
        output_format={"type": "json_schema", "schema": schema},
    )
    text_chunks: list[str] = []
    structured: Any = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and on_progress and block.name != "StructuredOutput":
                    on_progress(_tool_line(block.name, block.input or {}))
                elif hasattr(block, "text"):
                    text_chunks.append(block.text)
        elif isinstance(message, ResultMessage):
            structured = getattr(message, "structured_output", None)
            try:
                from app.core.database import SessionLocal
                from app.services import costs_service

                with SessionLocal() as udb:
                    costs_service.record(udb, source=usage_source, source_id=usage_id, model=model,
                                         total_cost_usd=message.total_cost_usd, duration_ms=message.duration_ms,
                                         num_turns=message.num_turns)
            except Exception:  # noqa: BLE001
                logger.debug("usage record failed", exc_info=True)
    if isinstance(structured, dict):
        return structured
    if isinstance(structured, str):
        return _extract_json(structured)
    return _extract_json("\n".join(text_chunks))


def _draft_prompt(req: dict[str, Any], book: dict[str, Any], error: str | None = None) -> str:
    account = req.get("account_kind") or "stocks_isa"
    holdings = [
        {"ticker": p["ticker"], "name": p["name"], "account": p["account_kind"], "value_gbp": round(p["value"]),
         "weight_of_book": round(p["weight"], 4)}
        for p in book["positions"][:30]
    ]
    payload = {
        "idea": req["idea"],
        "account": account,
        "conviction_hint": req.get("conviction"),
        "horizon_months": req.get("horizon_months") or 12,
        "budget_gbp_hint": req.get("budget_gbp"),
        "book": {
            "total_equity_gbp": round(book["total_gbp"]),
            "free_cash_gbp": {k: round(v) for k, v in book["free_cash_gbp"].items()},
            "holdings": holdings,
        },
        "today": date.today().isoformat(),
    }
    text = "Draft a Play for this idea. Context (JSON):\n" + json.dumps(payload, indent=1)
    if error:
        text += f"\n\nYour previous answer was invalid: {error}\nFix it and return valid JSON matching the schema."
    return text


def _draft_to_play(db: Session, req: dict[str, Any], out: PlayDraftOut, book: dict[str, Any]) -> Play:
    from app.services.config_store import ConfigStore

    legs = _normalise_legs([
        {"ticker": l.ticker.strip().upper(), "name": l.name, "role": l.role, "target_weight": l.weight,
         "rationale": l.rationale, "currency": listing_currency(l.ticker, l.currency)}
        for l in out.legs
    ])
    for l in legs:
        l["instrument_code"] = resolve_instrument(db, l["ticker"])
    conviction = req.get("conviction") or out.suggested_conviction
    play = Play(
        title=out.title.strip(),
        thesis=out.thesis.strip(),
        status="draft",
        conviction=conviction,
        horizon_months=int(req.get("horizon_months") or 12),
        account_kind=req.get("account_kind") or "stocks_isa",
        legs=legs,
        kill_criteria=[c.model_dump(exclude_none=True) for c in out.kill_criteria],
        what_would_change_my_mind=out.what_would_change_my_mind.strip(),
        review={},
        source_session_id=req.get("source_session_id"),
        meta={"idea": req["idea"], "benchmark": (out.benchmark or "SPY").upper(), "key_risks": out.key_risks,
              "conviction_source": "user" if req.get("conviction") else "archie"},
    )
    if req.get("budget_gbp"):
        play.budget_gbp = float(req["budget_gbp"])
        play.sizing = {"budget_override": True}
    db.add(play)
    db.flush()
    resize_play(db, play, risk=ConfigStore(db).get_risk(), book=book)
    db.commit()
    db.refresh(play)
    return play


async def _draft(job: DraftJob) -> None:
    from app.core.database import SessionLocal

    req = job.request
    with SessionLocal() as db:
        book = book_context(db)
        job.add(f"Book: {_fmt_gbp(book['total_gbp'])} across {len(book['positions'])} holdings")
        job.add("Researching the theme")
        schema = PlayDraftOut.model_json_schema()
        error: str | None = None
        for attempt in range(2):
            raw = await _run_structured(
                _draft_prompt(req, book, error), system=_DRAFT_SYSTEM, schema=schema,
                model=settings.claude_plays_model, tools=_DRAFT_TOOLS, mcp=_mcp_servers(), max_turns=24,
                on_progress=job.add, usage_source="play_draft", usage_id=job.id,
            )
            try:
                if raw is None:
                    raise ValueError("no JSON returned")
                out = PlayDraftOut.model_validate(raw)
                break
            except (ValidationError, ValueError) as exc:
                error = str(exc)[:1500]
                logger.warning("play draft invalid (attempt %d): %s", attempt + 1, error)
                if attempt == 0:
                    job.add("Tightening the draft")
        else:
            raise RuntimeError(f"Draft was invalid twice: {error}")
        job.add(f"Basket: {', '.join(l.ticker.upper() for l in out.legs)}")
        job.add("Sizing against your book and risk rails")
        play = _draft_to_play(db, req, out, book)
        job.play_id = play.id
        job.add("Draft ready")


def start_draft_job(request: dict[str, Any]) -> DraftJob:
    job = DraftJob(request)
    with _JOBS_LOCK:
        # Keep the registry small; finished jobs older than an hour are dropped.
        cutoff = _now() - timedelta(hours=1)
        for k in [k for k, j in _JOBS.items() if j.finished_at and j.finished_at < cutoff]:
            _JOBS.pop(k, None)
        _JOBS[job.id] = job

    def _run() -> None:
        try:
            asyncio.run(_draft(job))
            job.status = "done"
        except Exception as exc:  # noqa: BLE001
            logger.exception("play draft failed")
            job.status = "error"
            job.error = str(exc)[:500]
            job.add("Drafting failed")
        finally:
            job.finished_at = _now()

    threading.Thread(target=_run, name=f"play-draft-{job.id}", daemon=True).start()
    return job


_REVIEW_SYSTEM = """You review a user's theme bet ("Play") in 3-5 sentences. Be specific and calm.
Judge whether the thesis is on track given the data: performance vs benchmark, kill-rule status, drift,
and time elapsed vs horizon. verdict: on_track | watch | broken. Return only the structured JSON."""


def run_llm_review(db: Session, play: Play) -> dict[str, Any]:
    perf = play_performance(db, play)
    payload = {
        "play": {k: v for k, v in serialize_play(play).items() if k in {
            "title", "thesis", "conviction", "horizon_months", "activated_at", "legs", "kill_criteria",
            "what_would_change_my_mind", "budget_gbp"}},
        "performance": {k: perf.get(k) for k in (
            "held_gbp", "basket_return", "benchmark", "benchmark_return", "drawdown_pct", "tranches_staged",
            "tranches_total", "kill_status")},
        "legs": [{k: l.get(k) for k in ("ticker", "target_weight", "actual_weight", "value_gbp", "ppl_gbp")} for l in perf["legs"]],
        "today": date.today().isoformat(),
    }
    raw = asyncio.run(_run_structured(
        json.dumps(payload, default=str), system=_REVIEW_SYSTEM, schema=PlayReviewOut.model_json_schema(),
        model=settings.claude_plays_review_model, tools=[], mcp={}, max_turns=3, on_progress=None,
        usage_source="play_review", usage_id=play.id,
    ))
    out = PlayReviewOut.model_validate(raw or {})
    _append_review(play, out.verdict, out.notes, "archie")
    db.add(play)
    db.commit()
    db.refresh(play)
    return play.review
