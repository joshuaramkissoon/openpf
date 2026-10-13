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
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import Alert, Play, PlayDraftJob, TradeIntent
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
# Share of target exposure held before leg weights/drift are evaluated.
BUILT_THRESHOLD = 0.75
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
        "related": [r for r in (p.related or []) if not r.get("hidden")],
        "bench": p.bench or [],
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
            "price": float(p.get("current_price") or 0.0),
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
    overlap_gbp: float = 0.0,
    free_cash_gbp: float,
    risk: dict[str, Any],
    budget_override_gbp: float | None = None,
    max_play_pct: float = MAX_PLAY_PCT,
    horizon_months: int = 12,
    max_leg_weight: float | None = None,
    legs: list[dict[str, Any]] | None = None,
    held_by_leg: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Deterministic, explainable play size + staged entry.

    target exposure = book × conviction% × vol factor, capped at ``max_play_pct``;
    new money = target − what's already held in these names (per leg, a holding
    only counts up to that leg's slice); then capped by the account's free cash.
    A user-supplied budget wins but is flagged when above the cap.
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

    allocations: list[dict[str, Any]] = []
    if legs:
        held_by_leg = held_by_leg or {}
        wsum = sum(float(l.get("target_weight") or 0.0) for l in legs) or 1.0
        for l in legs:
            w = float(l.get("target_weight") or 0.0) / wsum
            slice_gbp = target_gbp * w
            held = float(held_by_leg.get(l["ticker"], 0.0))
            allocations.append({"ticker": l["ticker"], "target_gbp": round(slice_gbp, 2), "held_gbp": round(held, 2),
                                "new_gbp": round(max(slice_gbp - held, 0.0), 2), "weight": w})
        overlap_gbp = sum(min(a["held_gbp"], a["target_gbp"]) for a in allocations)
        suggested = sum(a["new_gbp"] for a in allocations)
        need = suggested or 1.0
        for a in allocations:
            a["pct_of_budget"] = round(a["new_gbp"] / need, 4) if suggested else round(a["weight"], 4)
        covered = [a for a in allocations if a["held_gbp"] > 0]
        if covered:
            names = ", ".join(f"{a['ticker']} ({_fmt_gbp(a['held_gbp'])})" for a in covered)
            steps.append(
                f"You already hold {names}; counted up to each leg's slice, that covers {_fmt_gbp(overlap_gbp)}, "
                f"so new money is {_fmt_gbp(suggested)}, weighted to the legs you don't own yet."
            )
        max_leg_weight = max((a["pct_of_budget"] for a in allocations), default=max_leg_weight)
    else:
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
        "allocations": allocations,
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
    its peak since activation, as a positive %), ``weights`` {ticker: share of
    the play, '__play__': play's share of the book}, ``today`` (date).
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
            v = v * 100 if v <= 1 else v  # accept 0.3 or 30
            if dd >= v:
                return {"state": "tripped", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
            if dd >= 0.75 * v:
                return {"state": "near", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
            return {"state": "intact", "detail": f"Basket down {dd:.1f}% from peak (limit {v:.0f}%)"}
        if rtype == "weight_above":
            # ticker → that leg's share of the play; no ticker → the play's share of the book.
            key = ticker or "__play__"
            w = (ctx.get("weights") or {}).get(key)
            if w is None:
                return {"state": "unknown", "detail": "Checked once the basket is built"}
            v = float(value)
            v = v / 100 if v > 1 else v
            label, of = (f"{ticker}", "of the play") if ticker else ("Play", "of book")
            msg = f"{label} is {w:.1%} {of} (max {v:.0%})"
            if w > v:
                return {"state": "tripped", "detail": msg}
            if w > 0.9 * v:
                return {"state": "near", "detail": msg}
            return {"state": "intact", "detail": msg}
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

def resolve_instrument(db: Session, ticker: str) -> tuple[str, bool]:
    """Best-effort T212 instrument code for a Yahoo-style ticker, plus whether it
    was found in the broker's instrument list (False → a guessed code)."""
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
                return code, True
            if not is_lse and code.upper().endswith("_US_EQ"):
                return code, True
    except Exception as exc:  # noqa: BLE001
        logger.debug("instrument search failed for %s: %s", ticker, exc)
    return (f"{base}l_EQ" if is_lse else f"{base}_US_EQ"), False


def instruments_known(db: Session) -> bool:
    """True when the broker's instrument list is available, so an unresolved
    ticker really means "not on Trading 212" rather than "couldn't check"."""
    try:
        from app.services.portfolio_service import _instrument_meta_map

        return bool(_instrument_meta_map(db))
    except Exception:  # noqa: BLE001
        return False


def _attach_instruments(db: Session, legs: list[dict[str, Any]]) -> None:
    for l in legs:
        if not l.get("instrument_code"):
            l["instrument_code"], l["instrument_verified"] = resolve_instrument(db, l["ticker"])


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


class PlayInputError(ValueError):
    """Bad play input — the message is friendly and shown to the user as-is."""


_ACCOUNTS = {"invest", "stocks_isa"}
MAX_LEGS = 12
_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-_]{0,15}")


def validate_play_input(data: dict[str, Any], *, creating: bool = False) -> None:
    """Friendly checks shared by the API, Archie's MCP tools and drafting."""
    if creating or data.get("title") is not None:
        title = str(data.get("title") or "").strip()
        if not title:
            raise PlayInputError("Give the play a short name.")
        if len(title) > 240:
            raise PlayInputError("That name is too long — keep it under 240 characters.")
    if data.get("conviction") is not None and data["conviction"] not in CONVICTION_PCT:
        raise PlayInputError("Conviction must be low, medium or high.")
    if data.get("horizon_months") is not None:
        try:
            h = int(data["horizon_months"])
        except (TypeError, ValueError):
            raise PlayInputError("Horizon must be a whole number of months.") from None
        if not 1 <= h <= 120:
            raise PlayInputError("Horizon must be between 1 and 120 months.")
    if data.get("account_kind") is not None and data["account_kind"] not in _ACCOUNTS:
        raise PlayInputError("Account must be Invest or Stocks ISA.")
    if data.get("budget_gbp") is not None:
        try:
            b = float(data["budget_gbp"])
        except (TypeError, ValueError):
            raise PlayInputError("Budget must be an amount in £.") from None
        if not math.isfinite(b) or b < 0:
            raise PlayInputError("Budget must be a positive amount in £.")
        if b > 10_000_000:
            raise PlayInputError("That budget is larger than any book Archie can size against.")
    if data.get("status") is not None and data["status"] not in _PLAY_STATUSES:
        raise PlayInputError("Status must be draft, active, paused or closed.")
    legs = data.get("legs")
    if legs is not None:
        if len(legs) > MAX_LEGS:
            raise PlayInputError(f"Keep a play to {MAX_LEGS} legs or fewer.")
        seen: set[str] = set()
        for l in legs:
            t = str(l.get("ticker") or "").strip().upper()
            if not t or not _TICKER_RE.fullmatch(t):
                raise PlayInputError(f"“{l.get('ticker') or ''}” doesn't look like a ticker (e.g. CCJ or NUCG.L).")
            if t in seen:
                raise PlayInputError(f"{t} appears twice in the basket.")
            seen.add(t)
            try:
                w = float(l.get("target_weight") or 0.0)
            except (TypeError, ValueError):
                raise PlayInputError(f"{t}'s weight must be a number.") from None
            if not math.isfinite(w) or w < 0:
                raise PlayInputError(f"{t}'s weight can't be negative.")
        if legs and sum(float(l.get("target_weight") or 0.0) for l in legs) <= 0:
            raise PlayInputError("Give at least one leg a weight above zero.")


_FIELD_MESSAGES = {
    "conviction": "Conviction must be low, medium or high.",
    "budget_gbp": "Budget must be a positive amount in £.",
    "horizon_months": "Horizon must be between 1 and 120 months.",
    "account_kind": "Account must be Invest or Stocks ISA.",
    "status": "Status must be draft, active, paused or closed.",
    "idea": "Keep the idea under 8,000 characters.",
    "tranche_index": "Pick a tranche from the plan.",
    "target_weight": "Leg weights must be between 0% and 100%.",
    "weight": "Weight must be between 1% and 50%.",
    "ticker": "Pick a ticker (e.g. CCJ or NUCG.L).",
    "owner": "Pick where this holding should go.",
    "title": "Give the play a short name (under 80 characters).",
}


def friendly_validation_message(errors: list[dict[str, Any]]) -> str:
    """One readable sentence for a FastAPI 422 on the plays API."""
    for err in errors:
        loc = [str(x) for x in err.get("loc") or [] if not str(x).isdigit()]
        field = next((f for f in reversed(loc) if f in _FIELD_MESSAGES), None)
        if field:
            return _FIELD_MESSAGES[field]
    if errors:
        loc = ".".join(str(x) for x in errors[0].get("loc") or [] if x != "body")
        return f"Check {loc or 'the request'}: {errors[0].get('msg', 'invalid value')}."
    return "Something in that request wasn't valid."


def unknown_tickers(db: Session, legs: list[dict[str, Any]]) -> list[str]:
    """Legs whose ticker isn't on Trading 212 (empty when the list can't be checked)."""
    if not legs or not instruments_known(db):
        return []
    return [l["ticker"] for l in legs if l.get("instrument_verified") is False]


def update_play(db: Session, play: Play, patch: dict[str, Any], *, risk: dict[str, Any] | None = None) -> Play:
    validate_play_input(patch)
    resize = bool(patch.pop("resize", False))
    for key in ("title", "thesis", "conviction", "horizon_months", "account_kind", "what_would_change_my_mind"):
        if patch.get(key) is not None:
            setattr(play, key, patch[key])
    if patch.get("legs") is not None:
        legs = _normalise_legs([dict(l) for l in patch["legs"]])
        _attach_instruments(db, legs)
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
    # A play counts holdings it owns (or Unsorted ones it would adopt) — never
    # ones owned by another play or Core, so nothing is double counted.
    try:
        from app.services import book_service

        owners = book_service.resolve_owners(book["positions"], book_service.owning_plays(db), book_service.explicit_rows(db))
        countable = book_service.available_for_play(play, book["positions"], owners)
    except Exception as exc:  # noqa: BLE001
        logger.info("ownership unavailable for sizing, using same-account overlap: %s", exc)
        countable = [p for p in book["positions"] if p.get("account_kind") == play.account_kind]
    overlap = overlap_with_book(play.legs or [], countable)
    overlap_any = overlap_with_book(play.legs or [], book["positions"])
    override = play.budget_gbp if (play.sizing or {}).get("budget_override") else None
    sizing = compute_sizing(
        book_gbp=book["total_gbp"],
        conviction=play.conviction,
        basket_vol=basket_volatility(play.legs or []),
        legs=play.legs or [],
        held_by_leg={o["ticker"]: o["value_gbp"] for o in overlap},
        free_cash_gbp=book["free_cash_gbp"].get(play.account_kind, 0.0),
        risk=risk,
        budget_override_gbp=override,
        horizon_months=play.horizon_months,
    )
    sizing["budget_override"] = bool(override)
    sizing["staged"] = (play.sizing or {}).get("staged", [])
    prior = play.sizing or {}
    if prior.get("adopted") and not override:
        # Adopted from holdings: the target stays at least what was adopted.
        sizing["adopted"] = True
        sizing["target_pct_of_book"] = max(float(prior.get("target_pct_of_book") or 0.0), sizing["target_pct_of_book"])
    if sizing["budget_gbp"] < MIN_LEG_NOTIONAL_GBP and not sizing["staged"]:
        # Already at (or past) target with what you hold — nothing to stage.
        sizing["tranches"] = []
        sizing["steps"] = [s for s in sizing.get("steps", []) if not s.startswith("Staged in")] + [
            "Holdings you already own cover the target, so no new money is planned."]
        sizing["rationale"] = " ".join(sizing["steps"])
    play.sizing = sizing
    play.budget_gbp = sizing["budget_gbp"]
    play.budget_pct_of_book = sizing["budget_pct_of_book"]
    meta = dict(play.meta or {})
    meta["overlap"] = overlap_any
    play.meta = meta


def create_play(db: Session, data: dict[str, Any], *, book: dict[str, Any] | None = None) -> Play:
    validate_play_input(data, creating=True)
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
        related=[],
        bench=[],
        meta={"origin": data["origin"]} if data.get("origin") else {},
    )
    legs = _normalise_legs([dict(l) for l in data.get("legs") or []])
    _attach_instruments(db, legs)
    play.legs = legs
    if data.get("budget_gbp"):
        play.budget_gbp = float(data["budget_gbp"])
        play.sizing = {"budget_override": True}
    db.add(play)
    db.flush()
    try:
        resize_play(db, play, book=book)
    except Exception as exc:  # noqa: BLE001 — sizing needs a portfolio; a manual play can still be saved
        logger.warning("sizing failed for new play: %s", exc)
    db.commit()
    db.refresh(play)
    return play


def delete_play(db: Session, play: Play) -> int:
    """Delete a play. Holdings it owned go to Unsorted; returns how many moved.
    Proposals it staged stay in the queue (they're labelled "not part of any play")."""
    from app.services import book_service

    moved = book_service.release_play(db, play)
    db.delete(play)
    db.commit()
    return moved


# ── Staging (proposals only — never executes) ────────────────────────────────

def _executed_notional_today(db: Session) -> float:
    try:
        from app.services.execution_service import _daily_executed_notional

        return float(_daily_executed_notional(db))
    except Exception:  # noqa: BLE001
        return 0.0


def _friendly_skip(error: str | None) -> str:
    e = (error or "").lower()
    if e.startswith("unknown instrument"):
        return "not available on Trading 212"
    if "could not price" in e or "convert" in e:
        return "no live price right now"
    if "too small" in e:
        return "below the minimum order size"
    return error or "not proposed"


def stage_tranche(
    db: Session,
    play: Play,
    tranche_index: int | None = None,
    *,
    risk: dict[str, Any] | None = None,
    free_cash_gbp: float | None = None,
) -> dict[str, Any]:
    """Turn the next (or given) tranche into ``proposed`` buy intents via the
    shared ``propose_trade`` path (same validation + rail checks as Archie's).
    Legs that would breach a hard rail are reported, not proposed."""
    from app.services import proposal_service
    from app.services.config_store import ConfigStore

    if play.status not in {"active", "draft"}:
        verb = "Resume" if play.status == "paused" else "Reopen"
        raise PlayInputError(f"This play is {play.status}. {verb} it before staging a tranche.")
    risk = risk or ConfigStore(db).get_risk()
    sizing = dict(play.sizing or {})
    tranches = sizing.get("tranches") or []
    staged = list(sizing.get("staged") or [])
    staged_idx = {s["index"] for s in staged}
    if not tranches or float(play.budget_gbp or 0.0) < MIN_LEG_NOTIONAL_GBP:
        raise PlayInputError("No new money is planned for this play — holdings you already own cover its target. "
                             "Raise the budget or conviction to add more.")
    if not play.legs:
        raise PlayInputError("This play has no basket yet — add some legs first.")
    if tranche_index is None:
        tranche_index = next((i for i in range(len(tranches)) if i not in staged_idx), None)
        if tranche_index is None:
            raise PlayInputError("Every tranche is already staged.")
    if tranche_index < 0 or tranche_index >= len(tranches):
        raise PlayInputError(f"There's no tranche {tranche_index + 1} — this play has {len(tranches)}.")
    if tranche_index in staged_idx:
        raise PlayInputError(f"Tranche {tranche_index + 1} is already staged.")

    tranche = tranches[tranche_index]
    tranche_gbp = float(play.budget_gbp) * float(tranche["pct_of_budget"])
    max_daily = float(risk.get("max_daily_notional") or 0.0)
    daily_used = _executed_notional_today(db)

    if free_cash_gbp is None:
        try:
            free_cash_gbp = book_context(db)["free_cash_gbp"].get(play.account_kind)
        except Exception:  # noqa: BLE001
            free_cash_gbp = None
    if free_cash_gbp is not None and free_cash_gbp < MIN_LEG_NOTIONAL_GBP:
        acct = "Stocks ISA" if play.account_kind == "stocks_isa" else "Invest account"
        raise PlayInputError(f"There's no free cash in your {acct} to fund this tranche. "
                             "Add funds or free some up, then stage it again.")

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    breaches_all: list[dict[str, Any]] = []
    running = 0.0
    alloc = {a["ticker"]: float(a["pct_of_budget"]) for a in sizing.get("allocations") or []}
    for leg in play.legs or []:
        notional = tranche_gbp * alloc.get(leg["ticker"], float(leg.get("target_weight") or 0.0))
        if notional < MIN_LEG_NOTIONAL_GBP:
            reason = "already held" if leg["ticker"] in alloc and alloc[leg["ticker"]] == 0 else "below minimum order size"
            skipped.append({"ticker": leg["ticker"], "reason": reason})
            continue
        # The play itself is carried in meta (play_id / play_title) and shown as the owner.
        rationale = (
            f"{tranche['label']} tranche · {leg.get('role', 'core')} leg, "
            f"{float(leg.get('target_weight') or 0):.0%} of the play. {leg.get('rationale', '')}"
        ).strip()
        res = proposal_service.propose_trade(
            db, side="buy", account_kind=play.account_kind, rationale=rationale,
            instrument_code=leg.get("instrument_code") or None,
            symbol=None if leg.get("instrument_code") else leg["ticker"],
            notional_gbp=round(notional, 2), play_id=play.id, source="play",
        )
        if not res.get("ok"):
            if res.get("violations"):
                breaches_all.append({"ticker": leg["ticker"], "breaches": res["violations"],
                                     "notional_gbp": round(notional, 2)})
            else:
                skipped.append({"ticker": leg["ticker"], "reason": _friendly_skip(res.get("error"))})
            continue
        intent_view = res["intent"]
        intent = db.get(TradeIntent, intent_view["id"])
        if intent is not None:
            intent.meta = {**(intent.meta or {}), "play_title": play.title, "tranche_index": tranche_index,
                           "tranche_label": tranche["label"], "leg_role": leg.get("role")}
            db.add(intent)
            db.commit()
        est = float(intent.estimated_notional if intent is not None else notional)
        running += est
        created.append({"intent_id": intent_view["id"], "ticker": leg["ticker"], "quantity": intent_view.get("quantity"),
                        "notional_gbp": round(est, 2), "warnings": res.get("warnings") or []})

    warnings: list[str] = []
    if max_daily and daily_used + running > max_daily:
        warnings.append(f"Approving all of this tranche today would pass your {_fmt_gbp(max_daily)} daily notional rail — "
                        "spread approvals over more than one day.")
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
    # Count what the play owns (any account) — see book_service for ownership.
    try:
        from app.services import book_service

        mine = book_service.owned_positions(db, play, book["positions"])
        if play.status == "draft":
            owners = book_service.resolve_owners(book["positions"], book_service.owning_plays(db), book_service.explicit_rows(db))
            mine = book_service.available_for_play(play, book["positions"], owners)
    except Exception as exc:  # noqa: BLE001
        logger.info("ownership unavailable for performance: %s", exc)
        mine = [p for p in book["positions"] if p.get("account_kind") == play.account_kind]
    legs_out = []
    held_total = 0.0
    for leg in play.legs or []:
        keys = _leg_keys(leg)
        held = [p for p in mine if _position_matches(p, keys)]
        value = sum(p["value"] for p in held)
        ppl = sum(p["ppl"] for p in held)
        held_total += value
        legs_out.append({**leg, "value_gbp": round(value, 2), "ppl_gbp": round(ppl, 2),
                         "weight_of_book": (value / book["total_gbp"]) if book["total_gbp"] else 0.0})
    for l in legs_out:
        l["actual_weight"] = (l["value_gbp"] / held_total) if held_total else 0.0
        l["drift"] = l["actual_weight"] - float(l.get("target_weight") or 0.0) if held_total else 0.0
    # Holdings the user moved into this play that aren't basket legs still count.
    off_basket = [
        {"ticker": p["ticker"], "name": p.get("name"), "account_kind": p.get("account_kind"),
         "value_gbp": round(p["value"], 2), "ppl_gbp": round(p["ppl"], 2)}
        for p in mine if not any(_position_matches(p, _leg_keys(l)) for l in play.legs or [])
    ]
    legs_value = held_total
    held_total += sum(o["value_gbp"] for o in off_basket)

    since_dt = play.activated_at or play.created_at or _now()
    benchmark = (play.meta or {}).get("benchmark") or "SPY"
    series = _basket_series(play.legs or [], since_dt.date(), benchmark, history_fn) if play.status != "draft" else {
        "points": [], "basket_return": None, "benchmark_return": None, "drawdown_pct": None}

    tranches = (play.sizing or {}).get("tranches") or []
    staged = (play.sizing or {}).get("staged") or []
    prices = {l["ticker"]: price_fn(l["ticker"]) for l in play.legs or [] if any(
        (c.get("rule") or {}).get("type") == "price_below" and (c.get("rule") or {}).get("ticker", "").upper() == l["ticker"]
        for c in play.kill_criteria or [])}
    # Leg weights/drift only mean something once the basket is mostly built —
    # mid-entry (or with a pre-existing holding) one leg naturally dominates.
    target_exposure = float(play.budget_gbp or 0.0) + float((play.sizing or {}).get("overlap_gbp") or 0.0)
    sz = play.sizing or {}
    fully_staged = len(sz.get("staged") or []) >= len(sz.get("tranches") or [])
    built = bool(fully_staged and held_total > 0 and target_exposure > 0
                 and held_total >= BUILT_THRESHOLD * target_exposure)
    weights = {l["ticker"]: l["actual_weight"] for l in legs_out} if built else {}
    if held_total > 0 and book["total_gbp"]:
        weights["__play__"] = held_total / book["total_gbp"]
    ctx = {"prices": prices, "drawdown_pct": series.get("drawdown_pct"), "weights": weights, "today": date.today()}
    kill_status = [{**c, **evaluate_rule(c.get("rule"), ctx)} for c in play.kill_criteria or []]

    return {
        "play_id": play.id,
        "held_gbp": round(held_total, 2),
        "target_exposure_gbp": round(target_exposure, 2),
        "basket_built": built,
        "budget_gbp": play.budget_gbp,
        "exposure_pct_of_budget": (held_total / play.budget_gbp) if play.budget_gbp else None,
        "legs": legs_out,
        "basket_gbp": round(legs_value, 2),
        "off_basket": off_basket,
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
        drifted = [l for l in perf["legs"] if perf["basket_built"] and abs(l["drift"]) > DRIFT_THRESHOLD]
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
  price_below {ticker, value in the listing currency}, drawdown_pct {value: % basket drawdown from peak, e.g. 30},
  weight_above {ticker: that leg's share of the play, e.g. 0.35; or ticker null: the whole play's share of
  the user's total book, e.g. 0.12},
  date_after {value: ISO date by which the thesis must have shown progress}.
- Do NOT size the position — the app sizes it deterministically against the user's book.
- "what_would_change_my_mind": one or two sentences, concrete and falsifiable.
- Thesis: markdown, 2-4 short paragraphs, plain English, no hype.
- If the idea is not an investable theme (gibberish, a pure goal like "make me rich", something with no listed
  instruments on Trading 212, or anything harmful), do NOT invent a basket: set "unclear" to one friendly sentence
  saying why and suggesting a sharper theme, and leave legs and kill_criteria empty.
Return only the structured JSON."""


# ── Idea sanity (before spending a model call) ──────────────────────────────

_GOAL_ONLY = re.compile(
    r"^(please\s+)?(make|get|help)?\s*(me\s+)?(rich|money|richer|wealthy|a millionaire|profit|profits|returns?)\b|"
    r"\b(beat the market|best stocks?|make (me )?money|get rich|10x|100x|to the moon|moon ?shot|free money|quick money)\b",
    re.I,
)
_MASH = {"asdf", "asdfgh", "qwerty", "qwer", "zxcv", "test", "testing", "hello", "hi", "foo", "bar", "lorem", "ipsum",
         "aaa", "xxx", "abc", "idk", "something", "anything", "stuff", "stocks", "shares", "invest", "investing"}


def idea_problem(idea: str | None) -> str | None:
    """A friendly reason the idea can't be drafted, or None if it's worth a try.
    Cheap and deterministic; the model still gets a chance to say "unclear"."""
    text = re.sub(r"\s+", " ", str(idea or "")).strip()
    if len(text) < 3:
        return "Describe the theme in a few words — e.g. “Europe is rearming for a decade”."
    if len(text) > 4000:
        return "That's a lot — keep the idea under 4,000 characters."
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text)
    letters = sum(len(w) for w in words)
    if not words or letters < 0.5 * len(text.replace(" ", "")):
        return "Archie couldn't find an idea in that. Describe what you believe will happen, in plain English."
    if len(words) <= 2 and all(w.lower() in _MASH or not re.search(r"[aeiouy]", w.lower()) or re.fullmatch(r"(.)\1+", w.lower()) for w in words):
        return "That doesn't read like a theme yet. What do you believe will happen — e.g. “GLP-1 drugs keep expanding”?"
    if _GOAL_ONLY.search(text) and len(words) <= 8:
        return ("That's a goal rather than a theme. Plays work best as a belief about the world — "
                "e.g. “AI datacentres need far more power” — and Archie sizes the risk for you.")
    return None


class DraftFailure(Exception):
    """A draft that can't succeed; ``message`` is friendly, ``kind`` drives the UI."""

    def __init__(self, message: str, kind: str = "failed"):
        super().__init__(message)
        self.message = message
        self.kind = kind


def _friendly_draft_error(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, DraftFailure):
        return exc.message, exc.kind
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if name in {"CLINotFoundError", "CLIConnectionError"} or "claude code not found" in low or "not logged in" in low \
            or "api key" in low or "authentication" in low or "401" in low:
        return ("Archie needs a Claude login to draft plays. Run `claude login` (or set ANTHROPIC_API_KEY), then retry.",
                "model")
    if "rate" in low and "limit" in low or "overloaded" in low or "529" in low:
        return "Claude is busy right now. Give it a minute and retry.", "model"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "Drafting took too long. Retry — it's usually quicker the second time.", "model"
    if "invalid twice" in low:
        return "Archie couldn't turn this into a sound basket. Try rephrasing the idea more specifically.", "failed"
    return "Archie hit a snag drafting this play. Retry, or rephrase the idea.", "failed"


# ── Draft jobs (persisted so restarts don't strand a spinner) ────────────────

STALE_JOB_MINUTES = 15
INTERRUPTED_MSG = "The server restarted while Archie was drafting this play. Retry to pick it up again."


def _session_factory():
    from app.core.database import SessionLocal

    return SessionLocal()


class DraftJob:
    """Write-through handle on a ``PlayDraftJob`` row (safe to use from the worker thread)."""

    def __init__(self, job_id: str, request: dict[str, Any] | None = None):
        self.id = job_id
        self.request = request or {}

    def _update(self, fn: Callable[[PlayDraftJob], None]) -> None:
        try:
            with _session_factory() as db:
                row = db.get(PlayDraftJob, self.id)
                if row is None:
                    return
                fn(row)
                row.updated_at = _now()
                db.add(row)
                db.commit()
        except Exception:  # noqa: BLE001 — progress is best-effort; never kill the draft over it
            logger.debug("draft job update failed", exc_info=True)

    def add(self, line: str) -> None:
        def _fn(row: PlayDraftJob) -> None:
            progress = list(row.progress or [])
            if progress and progress[-1].get("text") == line:
                return
            progress.append({"at": _now().isoformat(), "text": line})
            row.progress = progress
        self._update(_fn)

    def finish(self, *, play_id: str | None = None, error: str | None = None, error_kind: str | None = None) -> None:
        def _fn(row: PlayDraftJob) -> None:
            row.status = "error" if error else "done"
            row.play_id = play_id or row.play_id
            row.error = error
            row.error_kind = error_kind
            row.finished_at = _now()
        self._update(_fn)


def serialize_job(row: PlayDraftJob) -> dict[str, Any]:
    return {
        "job_id": row.id, "status": row.status, "progress": row.progress or [], "play_id": row.play_id,
        "error": row.error, "error_kind": row.error_kind,
        "retryable": row.status == "error" and row.error_kind != "unclear_idea",
        "idea": (row.request or {}).get("idea"), "request": row.request or {},
        "started_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


def _mark_interrupted(row: PlayDraftJob) -> None:
    row.status = "error"
    row.error = INTERRUPTED_MSG
    row.error_kind = "interrupted"
    row.finished_at = _now()
    row.progress = list(row.progress or []) + [{"at": _now().isoformat(), "text": "Interrupted"}]


def get_draft_job(db: Session, job_id: str) -> dict[str, Any] | None:
    row = db.get(PlayDraftJob, job_id)
    if row is None:
        return None
    # A worker that went silent (killed, crashed) without the startup sweep catching it.
    if row.status == "running" and row.updated_at and row.updated_at < _now() - timedelta(minutes=STALE_JOB_MINUTES):
        _mark_interrupted(row)
        db.add(row)
        db.commit()
    return serialize_job(row)


def fail_interrupted_jobs(db: Session) -> int:
    """Startup sweep: any job still 'running' was cut off by the restart."""
    rows = db.execute(select(PlayDraftJob).where(PlayDraftJob.status == "running")).scalars().all()
    for row in rows:
        _mark_interrupted(row)
        db.add(row)
    if rows:
        db.commit()
    return len(rows)


def _mcp_servers() -> dict[str, Any]:
    from app.services.mcp_registry import mcp_server_env, server_spec

    servers = {}
    for name in ("marketdata", "fundamentals"):
        spec = server_spec(name)
        if spec.script_path.is_file():
            servers[name] = {"type": "stdio", "command": sys.executable, "args": [str(spec.script_path)],
                             "env": mcp_server_env(spec)}
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
        tools=[t for t in tools if not t.startswith("mcp__")],
        allowed_tools=tools,
        mcp_servers=mcp,
        max_turns=max_turns,
        permission_mode="dontAsk",
        output_format={"type": "json_schema", "schema": schema},
    )
    text_chunks: list[str] = []
    structured: Any = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock) and on_progress and block.name not in {"StructuredOutput", "ToolSearch"}:
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
    _attach_instruments(db, legs)
    # Drop legs Trading 212 doesn't list; a basket needs at least two real ones.
    missing = unknown_tickers(db, legs)
    if missing:
        kept = [l for l in legs if l["ticker"] not in missing]
        if len(kept) < 2:
            acct = "Stocks ISA" if (req.get("account_kind") or "stocks_isa") == "stocks_isa" else "Invest account"
            raise DraftFailure(
                f"Archie couldn't find enough of this theme on Trading 212 ({', '.join(missing)} aren't listed for your "
                f"{acct}). Try a broader angle on the idea, or the other account.", "no_instruments")
        legs = _normalise_legs(kept)
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
        related=[],
        bench=[],
        meta={"idea": req["idea"], "benchmark": (out.benchmark or "SPY").upper(), "key_risks": out.key_risks,
              "conviction_source": "user" if req.get("conviction") else "archie",
              **({"dropped_legs": missing} if missing else {})},
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


async def _draft(job: DraftJob) -> str:
    """Research → validate → size → save. Returns the new play id; raises
    ``DraftFailure`` for outcomes the user should see as-is."""
    req = job.request
    with _session_factory() as db:
        try:
            book = book_context(db)
        except Exception as exc:  # noqa: BLE001
            raise DraftFailure("Archie couldn't read your portfolio just now. Check the broker connection, then retry.",
                               "failed") from exc
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
        if out.unclear:
            raise DraftFailure(out.unclear.strip()[:400], "unclear_idea")
        job.add(f"Basket: {', '.join(l.ticker.upper() for l in out.legs)}")
        job.add("Checking it's all on Trading 212")
        job.add("Sizing against your book and risk rails")
        play = _draft_to_play(db, req, out, book)
        job.add("Draft ready")
        return play.id


def run_draft_job(job: DraftJob) -> None:
    """Blocking body of a draft job (runs on a worker thread)."""
    try:
        play_id = asyncio.run(_draft(job))
        job.finish(play_id=play_id)
    except Exception as exc:  # noqa: BLE001
        message, kind = _friendly_draft_error(exc)
        if not isinstance(exc, DraftFailure):
            logger.exception("play draft failed")
        job.add("Couldn't draft this one" if kind == "unclear_idea" else "Drafting stopped")
        job.finish(error=message, error_kind=kind)


def start_draft_job(db: Session, request: dict[str, Any], *, retry_of: str | None = None, run: bool = True) -> dict[str, Any]:
    """Validate the idea, persist a job and start drafting in the background."""
    problem = idea_problem(request.get("idea"))
    if problem:
        raise PlayInputError(problem)
    request = {**request, "idea": re.sub(r"\s+", " ", str(request["idea"])).strip()}
    validate_play_input({k: request.get(k) for k in ("conviction", "horizon_months", "account_kind", "budget_gbp")})
    row = PlayDraftJob(id=uuid.uuid4().hex[:12], status="running", request=request, retry_of=retry_of,
                       progress=[{"at": _now().isoformat(), "text": "Reading your portfolio"}])
    db.add(row)
    db.commit()
    db.refresh(row)
    if run:
        job = DraftJob(row.id, request)
        threading.Thread(target=run_draft_job, args=(job,), name=f"play-draft-{row.id}", daemon=True).start()
    return serialize_job(row)


def retry_draft_job(db: Session, job_id: str, *, run: bool = True) -> dict[str, Any]:
    row = db.get(PlayDraftJob, job_id)
    if row is None:
        raise PlayInputError("That draft has gone — start a new one.")
    if row.status == "running":
        raise PlayInputError("This draft is still running.")
    if row.status == "done" and row.play_id:
        raise PlayInputError("This draft already finished.")
    return start_draft_job(db, dict(row.request or {}), retry_of=row.id, run=run)


_REVIEW_SYSTEM ="""You review a user's theme bet ("Play") in 3-5 sentences. Be specific and calm.
Judge whether the thesis is on track given the data: performance vs benchmark, kill-rule status, drift,
and time elapsed vs horizon. verdict: on_track | watch | broken. Return only the structured JSON."""


def run_llm_review(db: Session, play: Play) -> dict[str, Any]:
    perf = play_performance(db, play)
    payload = {
        "play": {k: v for k, v in serialize_play(play).items() if k in {
            "title", "thesis", "conviction", "horizon_months", "activated_at", "legs", "kill_criteria",
            "what_would_change_my_mind", "budget_gbp"}},
        "performance": {k: perf.get(k) for k in (
            "held_gbp", "target_exposure_gbp", "basket_return", "benchmark", "benchmark_return", "drawdown_pct",
            "tranches_staged", "tranches_total", "kill_status")},
        "note": "Staged tranches are buy proposals awaiting the user's approval; held values only reflect filled orders "
                "and may include holdings that pre-date the play.",
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
