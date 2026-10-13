"""Agent trade proposals — the only way Archie can express a trade.

``propose_trade`` validates and sizes a trade, checks it against the execution
rails, and records it as a ``proposed`` TradeIntent. It never talks to the
broker: a human approves the intent and ``execution_service.execute_intent``
(which re-checks every rail) places it.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import TradeIntent
from app.services.config_store import ConfigStore
from app.services.execution_service import (
    daily_notional_used,
    expire_stale_intents,
    rail_violations,
    utc_today,
)

_SIDES = {"buy", "sell"}
_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}
_ACCOUNTS = {"invest", "stocks_isa"}
# Cash can be stale between snapshots, so a shortfall is reported but doesn't
# block the proposal (execute_intent re-checks it against the latest snapshot).
_SOFT_VIOLATIONS = ("risk-guard: insufficient available cash",)
# The two notional limits. An ask over them can be split: the part that fits is
# proposed and the rest is held back for the user (never loosened by Archie).
_PER_ORDER = "risk-guard: order exceeds max single-order notional"
_DAILY = "risk-guard: order exceeds max daily notional"
_LIMIT_VIOLATIONS = (_PER_ORDER, _DAILY)
# Below this, the part that fits isn't worth an order of its own: hold it all.
_MIN_SPLIT_GBP = 10.0


class ProposalError(ValueError):
    pass


class AmbiguousInstrument(ProposalError):
    """The symbol matches several listings (or none exactly) — the caller must
    pick one by instrument_code. ``candidates`` lists the options."""

    def __init__(self, message: str, candidates: list[dict[str, Any]]):
        super().__init__(message)
        self.candidates = candidates


# Upper bound on a single proposal's share count — anything beyond is a unit
# mistake (e.g. pence vs pounds), not a trade.
_MAX_QUANTITY = 1_000_000


def _resolve_instrument(db: Session, symbol: str | None, instrument_code: str | None) -> dict[str, Any] | None:
    """``{instrument_code, ticker, currency, name, verified}`` or None if unknown.

    Uses the cached T212 instrument metadata. If metadata is unavailable (no
    read key / offline) an explicit instrument_code is accepted unverified."""
    from app.services.portfolio_service import _instrument_meta_map, search_instruments

    meta = _instrument_meta_map(db)
    if instrument_code:
        info = meta.get(instrument_code.strip().upper())
        if info:
            code = info.get("ticker") or instrument_code.strip()
            return {
                "instrument_code": code,
                "ticker": code.upper().split("_", 1)[0],
                "currency": info.get("currency") or None,
                "name": info.get("name") or "",
                "short_name": info.get("short_name") or "",
                "verified": True,
            }
        if not meta:
            code = instrument_code.strip()
            return {
                "instrument_code": code,
                "ticker": code.upper().split("_", 1)[0],
                "currency": None,
                "name": "",
                "short_name": "",
                "verified": False,
            }
        return None
    from app.services.portfolio_service import _LEVERAGE_NAME_RE

    term = (symbol or "").strip().upper()
    rows = search_instruments(term, db, limit=25)
    if not rows:
        return None
    # Only an EXACT symbol/code match may be traded by symbol: the search also
    # returns prefix and name matches ('bitcoin' -> some 'Bitcoin ...' ETP),
    # which must never be silently substituted for what was asked.
    exact = [
        r for r in rows
        if term in {str(r.get("ticker") or "").upper(), str(r.get("display_ticker") or "").upper(),
                    str(r.get("instrument_code") or "").upper()}
    ]
    candidates = [
        {"instrument_code": r["instrument_code"], "ticker": r.get("display_ticker") or r["ticker"],
         "name": r.get("name") or "", "currency": r.get("currency")}
        for r in (exact or rows)[:6]
    ]
    if not exact:
        raise AmbiguousInstrument(
            f"no instrument with the exact symbol {term!r} — closest matches are listed; "
            "pass the right one as instrument_code (or tell the user it isn't tradable on Trading 212)",
            candidates,
        )
    # A same-symbol leveraged/ETP wrapper doesn't make a plain stock ambiguous.
    plain = [r for r in exact if not _LEVERAGE_NAME_RE.search(r.get("name") or "")] or exact
    if len({r["instrument_code"] for r in plain}) > 1:
        raise AmbiguousInstrument(
            f"{term!r} is listed on more than one exchange — pass the intended listing as instrument_code",
            candidates,
        )
    row = plain[0]
    info = meta.get(str(row["instrument_code"]).upper(), {})
    return {
        "instrument_code": info.get("ticker") or row["instrument_code"],
        "ticker": row.get("display_ticker") or row["ticker"],
        "currency": row.get("currency"),
        "name": row.get("name") or "",
        "short_name": info.get("short_name") or "",
        "verified": True,
    }


def _reference_price(instrument: dict[str, Any]) -> tuple[float, str] | None:
    """Last close in the instrument's quote currency, or None if unavailable."""
    from app.services.market_data import fetch_history
    from app.services.portfolio_service import market_yfinance_ticker

    yf = market_yfinance_ticker(instrument["instrument_code"], instrument.get("short_name"), instrument.get("currency"))
    if not yf:
        return None
    try:
        history = fetch_history(yf, lookback_days=10)
        price = float(history["close"].iloc[-1])
    except Exception:  # noqa: BLE001 — pricing is best-effort; caller handles None
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return price, str(instrument.get("currency") or "USD")


def _to_gbp(amount: float, currency: str) -> float:
    from app.services.fx import get_fx_rate

    return amount * get_fx_rate(currency, "GBP")


def _coerce_positive(value: Any, name: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ProposalError(f"{name} must be a number") from exc
    if out == 0:
        return None  # 0 means "not passed" (MCP tool defaults)
    if not math.isfinite(out) or out < 0:
        raise ProposalError(f"{name} must be a positive number")
    return out


def _day_label(day: date) -> str:
    today = utc_today()
    if day <= today:
        return "today's"
    return "tomorrow's" if day == today + timedelta(days=1) else f"{day:%a %-d %b}'s"


def _gbp(v: float) -> str:
    return f"£{v:,.0f}"


def limit_blocks(risk: dict[str, Any], violations: list[str], requested: float, used: float, day: date) -> list[dict[str, Any]]:
    """Structured form of the notional-limit violations, for the agent and the
    chat's held-back cards: which limit, its size, what's used, what's left."""
    out: list[dict[str, Any]] = []
    single = float(risk["max_single_order_notional"])
    daily = float(risk["max_daily_notional"])
    if _PER_ORDER in violations:
        out.append({
            "rail": "max_single_order_notional", "label": "per-order limit", "limit_gbp": single,
            "requested_gbp": round(requested, 2), "used_gbp": None, "remaining_gbp": single,
            "message": f"{_gbp(requested)} is over your {_gbp(single)} per-order limit",
        })
    if _DAILY in violations:
        remaining = max(0.0, daily - used)
        out.append({
            "rail": "max_daily_notional", "label": "daily limit", "limit_gbp": daily,
            "requested_gbp": round(requested, 2), "used_gbp": round(used, 2), "remaining_gbp": round(remaining, 2),
            "for_day": day.isoformat(),
            "message": (f"{_day_label(day)} {_gbp(daily)} limit is used" if remaining < 1
                        else f"over {_day_label(day)} {_gbp(daily)} limit ({_gbp(remaining)} left)"),
        })
    return out


def _is_crypto(account_kind: str, symbol: str | None, instrument_code: str | None) -> bool:
    from app.services import crypto_service

    return account_kind == "crypto" or (not account_kind and crypto_service.is_crypto_symbol(symbol or instrument_code))


def _store(db: Session, intent: TradeIntent, into: TradeIntent | None) -> TradeIntent:
    """Persist a new intent, or (staging a held-back ask) turn the held row into
    it so the card that showed the held ask now shows the proposal."""
    if into is None:
        db.add(intent)
    else:
        for col in ("status", "broker_mode", "symbol", "instrument_code", "side", "order_type", "quantity",
                    "estimated_notional", "limit_price", "stop_price", "rationale", "meta", "not_before"):
            setattr(into, col, getattr(intent, col))
        into.created_at = datetime.utcnow()
        into.failure_reason = None
        intent = into
    db.commit()
    db.refresh(intent)
    return intent


def propose_trade(
    db: Session,
    *,
    side: str,
    account_kind: str,
    rationale: str,
    symbol: str | None = None,
    instrument_code: str | None = None,
    quantity: float | None = None,
    notional_gbp: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    play_id: str | None = None,
    source: str = "agent",
    category: str | None = None,
    not_before: date | None = None,
    hold_back: bool = False,
) -> dict[str, Any]:
    """Validate, size and record a ``proposed`` TradeIntent. Never executes.

    Returns ``{"ok": True, "intent": {...}, "warnings": [...], "caps": {...}}`` or
    ``{"ok": False, "error"|"violations": ..., "caps": {...}}`` — hard rail
    violations (single-order / daily notional caps, duplicates) are returned
    without creating an intent so the agent can resize and retry. Notional-limit
    violations also come back structured under ``blocked``.

    ``not_before``: stage it for a later day's limit window (it can't be placed
    before then, and counts against that day's daily cap).

    ``hold_back``: when only the notional limits stand in the way, propose the
    part that fits (counting today's open proposals against the daily cap) and
    record the rest as a ``held`` ask under ``held_back`` — the chat shows it as
    a card the user can stage for tomorrow or take to their limits. The limits
    themselves are never changed.
    """
    day = not_before or utc_today()
    used = daily_notional_used(db, day, include_pending=hold_back)
    args = dict(
        side=side, account_kind=account_kind, rationale=rationale, symbol=symbol, instrument_code=instrument_code,
        order_type=order_type, limit_price=limit_price, stop_price=stop_price, play_id=play_id, source=source,
        category=category, not_before=not_before,
    )
    res = _propose_one(db, quantity=quantity, notional_gbp=notional_gbp, daily_used=used, **args)
    violations = res.get("violations") or []
    if not hold_back or res.get("ok") or not violations or any(v not in _LIMIT_VIOLATIONS for v in violations):
        return res

    risk = ConfigStore(db).get_risk()
    sizing = res["sizing"]
    requested = float(sizing["estimated_notional_gbp"])
    room = [float(risk["max_single_order_notional"]), float(risk["max_daily_notional"]) - used]
    fits = math.floor(max(0.0, min(room)))
    now: dict[str, Any] | None = None
    if fits >= _MIN_SPLIT_GBP and fits < requested:
        now = _propose_one(db, quantity=None, notional_gbp=float(fits), daily_used=used, **args)
        if not now.get("ok"):
            return res  # something else stops even the smaller order: report the original
    proposed_gbp = float(now["intent"]["estimated_notional_gbp"]) if now else 0.0
    held_gbp = round(requested - proposed_gbp, 2)
    blocked = res["blocked"]
    reason = "; ".join(b["message"] for b in blocked)
    held = _hold(db, args, sizing, held_gbp, requested=requested, blocked=blocked, reason=reason, day=day)
    held_info = {
        "id": held.id, "symbol": held.symbol, "side": held.side, "amount_gbp": held_gbp,
        "reason": reason, "blocked": blocked,
    }
    if now:
        message = (f"{reason}. Proposed {_gbp(proposed_gbp)} now; the other {_gbp(held_gbp)} is held back. "
                   "The held-back card lets the user stage it for tomorrow or change their limits — "
                   "say which limit cut it in one line; don't re-propose it or suggest loosening limits.")
        return {**now, "held_back": held_info, "blocked": blocked,
                "message": message, "requested_gbp": round(requested, 2)}
    message = (f"Nothing proposed: {reason}. The {_gbp(held_gbp)} is held back on a card the user can stage for "
               "tomorrow or take to their limits — say which limit cut it in one line; don't loosen limits.")
    return {"ok": False, "violations": violations, "blocked": blocked, "sizing": sizing, "caps": res["caps"],
            "held_back": held_info, "message": message, "requested_gbp": round(requested, 2)}


def _hold(
    db: Session, args: dict[str, Any], sizing: dict[str, Any], amount: float, *, requested: float,
    blocked: list[dict[str, Any]], reason: str, day: date,
) -> TradeIntent:
    """Record the part of an ask a limit stopped as a ``held`` intent. It can't be
    approved or placed; staging it (``stage_held``) re-proposes it for a later day."""
    crypto = _is_crypto((args.get("account_kind") or "").strip().lower(), args.get("symbol"), args.get("instrument_code"))
    account = "crypto" if crypto else (args.get("account_kind") or "").strip().lower()
    unit = float(sizing["estimated_notional_gbp"]) / float(sizing["quantity"])
    scale = 1e8 if crypto else 1e6
    qty = math.floor((amount / unit) * scale) / scale if unit > 0 else 0.0
    request = {k: v for k, v in args.items() if k not in {"rationale", "not_before"} and v not in (None, "")}
    meta: dict[str, Any] = {
        "source": (args.get("source") or "agent")[:64],
        "account_kind": account,
        "reference_price": sizing.get("reference_price"),
        "currency": sizing.get("currency"),
        "price_gbp": round(unit, 6),
        "instrument_name": sizing.get("name") or "",
        "held": {"blocked": blocked, "reason": reason, "requested_gbp": round(requested, 2), "for_day": day.isoformat()},
        "request": request,
    }
    play_id = args.get("play_id")
    if play_id and str(play_id).strip().lower() == "core":
        meta["owner"] = "core"
    elif play_id:
        from app.models.entities import Play

        play = db.get(Play, str(play_id).strip())
        if play is not None:
            meta["play_id"] = play.id
            meta["play_title"] = play.title
    intent = TradeIntent(
        status="held",
        broker_mode=str(ConfigStore(db).get_broker().get("broker_mode", "paper")),
        symbol=str(sizing["symbol"])[:32],
        instrument_code=str(sizing["instrument_code"])[:64],
        side=(args.get("side") or "").strip().lower(),
        order_type=(args.get("order_type") or "market").strip().lower(),
        quantity=qty,
        estimated_notional=round(amount, 2),
        limit_price=args.get("limit_price"),
        stop_price=args.get("stop_price"),
        rationale=(args.get("rationale") or "")[:8000],
        meta=meta,
    )
    return _store(db, intent, None)


def stage_held(db: Session, intent_id: str, day: date | None = None) -> dict[str, Any]:
    """Re-propose a held-back ask for a later day's limit window (tomorrow by
    default). The held row becomes the proposal, dated ``not_before`` that day;
    every rail is checked for that day as usual. Limits are never changed."""
    intent = db.get(TradeIntent, intent_id)
    if intent is None:
        return {"ok": False, "error": "not found"}
    if intent.status != "held":
        return {"ok": False, "error": f"this ask is already {intent.status}"}
    day = day or utc_today() + timedelta(days=1)
    if day <= utc_today():
        return {"ok": False, "error": "a held-back ask can only be staged for a later day"}
    meta = intent.meta or {}
    request = dict(meta.get("request") or {})
    request.pop("hold_back", None)
    res = _propose_one(
        db,
        **{**request, "rationale": intent.rationale},
        quantity=None,
        notional_gbp=float(intent.estimated_notional),
        not_before=day,
        daily_used=daily_notional_used(db, day),
        into=intent,
        held_from=meta.get("held"),
    )
    if not res.get("ok") and res.get("blocked"):
        res["error"] = "; ".join(b["message"] for b in res["blocked"])
    return res


def _propose_one(
    db: Session,
    *,
    side: str,
    account_kind: str,
    rationale: str,
    symbol: str | None = None,
    instrument_code: str | None = None,
    quantity: float | None = None,
    notional_gbp: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    stop_price: float | None = None,
    play_id: str | None = None,
    source: str = "agent",
    category: str | None = None,
    not_before: date | None = None,
    daily_used: float | None = None,
    into: TradeIntent | None = None,
    held_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    side = (side or "").strip().lower()
    order_type = (order_type or "market").strip().lower()
    account_kind = (account_kind or "").strip().lower()
    rationale = (rationale or "").strip()
    risk = ConfigStore(db).get_risk()
    day = not_before or utc_today()
    if daily_used is None:
        daily_used = daily_notional_used(db, day)
    caps = {
        "max_single_order_notional_gbp": float(risk["max_single_order_notional"]),
        "max_daily_notional_gbp": float(risk["max_daily_notional"]),
        "daily_used_gbp": round(daily_used, 2),
        "for_day": day.isoformat(),
    }
    if not_before is not None and not_before < utc_today():
        return {"ok": False, "error": "not_before can't be in the past", "caps": caps}

    def _fail(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message, "caps": caps}

    if side not in _SIDES:
        return _fail("side must be 'buy' or 'sell'")
    if order_type not in _ORDER_TYPES:
        return _fail(f"order_type must be one of {sorted(_ORDER_TYPES)}")
    # Crypto routes to the connected exchange (crypto_service); same rails + cap.
    from app.services import crypto_service

    if _is_crypto(account_kind, symbol, instrument_code):
        if not rationale:
            return _fail("rationale is required — say why, and what would invalidate the idea")
        try:
            qty_c, notional_c = _coerce_positive(quantity, "quantity"), _coerce_positive(notional_gbp, "notional_gbp")
        except ProposalError as exc:
            return _fail(str(exc))
        if (qty_c is None) == (notional_c is None):
            return _fail("pass exactly one of quantity (coins) or notional_gbp (GBP amount)")
        return crypto_service.propose_crypto_trade(
            db, side=side, rationale=rationale, symbol=symbol, instrument_code=instrument_code, quantity=qty_c,
            notional_gbp=notional_c, order_type=order_type, play_id=play_id, source=source, caps=caps, risk=risk,
            not_before=not_before, daily_used=daily_used, into=into, held_from=held_from,
        )
    if account_kind in _ACCOUNTS and (hint := crypto_service.wrong_account_hint(symbol, instrument_code)):
        return _fail(hint)
    if account_kind not in _ACCOUNTS:
        return _fail("account_kind must be 'invest', 'stocks_isa' or 'crypto' (no default — say which account)")
    if not rationale:
        return _fail("rationale is required — say why, and what would invalidate the idea")
    if not (symbol or instrument_code):
        return _fail("pass symbol or instrument_code")
    try:
        qty = _coerce_positive(quantity, "quantity")
        notional = _coerce_positive(notional_gbp, "notional_gbp")
        limit = _coerce_positive(limit_price, "limit_price")
        stop = _coerce_positive(stop_price, "stop_price")
    except ProposalError as exc:
        return _fail(str(exc))
    if (qty is None) == (notional is None):
        return _fail("pass exactly one of quantity (shares) or notional_gbp (GBP amount)")
    if order_type in {"limit", "stop_limit"} and limit is None:
        return _fail(f"{order_type} orders need limit_price")
    if order_type in {"stop", "stop_limit"} and stop is None:
        return _fail(f"{order_type} orders need stop_price")

    if qty is not None and qty > _MAX_QUANTITY:
        return _fail(f"quantity {qty:g} is implausibly large — check the units (shares, not pence/cents)")

    try:
        instrument = _resolve_instrument(db, symbol, instrument_code)
    except AmbiguousInstrument as exc:
        return {"ok": False, "error": str(exc), "candidates": exc.candidates, "caps": caps}
    if not instrument:
        return _fail(f"unknown instrument {instrument_code or symbol!r} — search_instruments for the exact code")

    warnings: list[str] = []
    if not instrument["verified"]:
        warnings.append("instrument metadata unavailable — instrument_code accepted unverified")

    # Price per share in the quote currency: the order's own price if it has one,
    # else the last close.
    currency = str(instrument.get("currency") or "")
    price = limit if limit is not None else stop
    if price is None:
        quote = _reference_price(instrument)
        if quote is None:
            return _fail("could not price this instrument — pass limit_price or try another listing")
        price, currency = quote
    currency = currency or "USD"
    try:
        price_gbp = _to_gbp(price, currency)
    except Exception:  # noqa: BLE001
        return _fail(f"could not convert {currency} to GBP")
    if price_gbp <= 0:
        return _fail("could not price this instrument")

    if qty is None:
        qty = math.floor((notional / price_gbp) * 1e6) / 1e6  # type: ignore[operator]
        if qty <= 0:
            return _fail("notional_gbp is too small for one fractional unit")
    if qty > _MAX_QUANTITY or not math.isfinite(qty):
        return _fail(f"that sizes to {qty:g} shares — implausibly large; check the amount")
    estimated_notional = round(qty * price_gbp, 2)

    ticker = str(instrument["ticker"]).upper()[:32]
    violations = rail_violations(
        db,
        symbol=ticker,
        side=side,
        quantity=qty,
        estimated_notional=estimated_notional,
        risk=risk,
        account_kind=account_kind,
        instrument_code=str(instrument["instrument_code"]),
        not_before=not_before,
        daily_used=daily_used,
    )
    hard = [v for v in violations if v not in _SOFT_VIOLATIONS]
    warnings.extend(v for v in violations if v in _SOFT_VIOLATIONS)
    sizing = {
        "symbol": ticker,
        "instrument_code": instrument["instrument_code"],
        "quantity": qty,
        "reference_price": price,
        "currency": currency,
        "estimated_notional_gbp": estimated_notional,
        "name": instrument.get("name") or "",
    }
    if hard:
        return {"ok": False, "violations": hard, "sizing": sizing, "caps": caps,
                "blocked": limit_blocks(risk, hard, estimated_notional, daily_used, day)}

    # "rebalance" is reserved for the deterministic rebalancer (a standing
    # rebalance permission keys off it); proposals can't claim it.
    source = (source or "agent")[:64]
    if source.strip().lower() == "rebalance":
        source = "agent"
    meta: dict[str, Any] = {
        "source": source,
        "account_kind": account_kind,
        "reference_price": price,
        "currency": currency,
        "price_gbp": round(price_gbp, 6),
        "instrument_name": instrument.get("name") or "",
    }
    if notional is not None:
        meta["requested_notional_gbp"] = notional
    # "For which play?" — a play id, or "core" for long-term / no-thesis holdings.
    if play_id and str(play_id).strip().lower() == "core":
        meta["owner"] = "core"
    elif play_id:
        from app.models.entities import Play

        play = db.get(Play, str(play_id).strip())
        if play is None:
            warnings.append(f"play {play_id!r} not found — proposal is not linked to a play")
        else:
            meta["play_id"] = play.id
            meta.setdefault("play_title", play.title)
    # What kind of call this is (track record + standing permissions). An
    # explicit category wins; otherwise it's inferred from source/play/rationale.
    from app.services import intent_category

    meta["category"] = intent_category.normalize(category) or intent_category.infer(
        side=side, source=meta["source"], rationale=rationale, meta=meta
    )
    if warnings:
        meta["warnings"] = warnings
    if held_from:
        meta["held"] = {**held_from, "staged": True}

    broker = ConfigStore(db).get_broker()
    intent = TradeIntent(
        status="proposed",
        broker_mode=str(broker.get("broker_mode", "paper")),
        symbol=ticker,
        instrument_code=str(instrument["instrument_code"])[:64],
        side=side,
        order_type=order_type,
        quantity=qty,
        estimated_notional=estimated_notional,
        limit_price=limit,
        stop_price=stop,
        rationale=rationale[:8000],
        meta=meta,
        not_before=not_before,
    )
    intent = _store(db, intent, into)
    return {
        "ok": True,
        "intent": serialize_intent(intent),
        "warnings": warnings,
        "caps": caps,
        "note": "Proposed only — nothing was sent to the broker. A human must approve it in the app.",
    }


def serialize_intent(intent: TradeIntent) -> dict[str, Any]:
    meta = intent.meta or {}
    return {
        "id": intent.id,
        "created_at": intent.created_at.isoformat() if intent.created_at else None,
        "status": intent.status,
        "symbol": intent.symbol,
        "instrument_code": intent.instrument_code,
        "side": intent.side,
        "order_type": intent.order_type,
        "quantity": intent.quantity,
        "estimated_notional_gbp": intent.estimated_notional,
        "limit_price": intent.limit_price,
        "stop_price": intent.stop_price,
        "account_kind": meta.get("account_kind"),
        "play_id": meta.get("play_id"),
        "owner": "core" if meta.get("owner") == "core" else ("play" if meta.get("play_id") else None),
        "crypto": meta.get("crypto"),
        "source": meta.get("source"),
        "category": meta.get("category"),
        "approved_by": getattr(intent, "approved_by", None),
        "rationale": intent.rationale,
        "failure_reason": intent.failure_reason,
        "not_before": intent.not_before.isoformat() if intent.not_before else None,
        "held": meta.get("held"),
    }


def list_proposals(db: Session, *, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
    expire_stale_intents(db)
    q = select(TradeIntent).order_by(desc(TradeIntent.created_at)).limit(max(1, min(int(limit or 20), 100)))
    if status and status != "all":
        q = q.where(TradeIntent.status == status)
    return [serialize_intent(i) for i in db.execute(q).scalars().all()]
