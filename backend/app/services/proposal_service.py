"""Agent trade proposals — the only way Archie can express a trade.

``propose_trade`` validates and sizes a trade, checks it against the execution
rails, and records it as a ``proposed`` TradeIntent. It never talks to the
broker: a human approves the intent and ``execution_service.execute_intent``
(which re-checks every rail) places it.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import TradeIntent
from app.services.config_store import ConfigStore
from app.services.execution_service import _daily_executed_notional, expire_stale_intents, rail_violations

_SIDES = {"buy", "sell"}
_ORDER_TYPES = {"market", "limit", "stop", "stop_limit"}
_ACCOUNTS = {"invest", "stocks_isa"}
# Cash can be stale between snapshots, so a shortfall is reported but doesn't
# block the proposal (execute_intent re-checks it against the latest snapshot).
_SOFT_VIOLATIONS = ("risk-guard: insufficient available cash",)


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
) -> dict[str, Any]:
    """Validate, size and record a ``proposed`` TradeIntent. Never executes.

    Returns ``{"ok": True, "intent": {...}, "warnings": [...], "caps": {...}}`` or
    ``{"ok": False, "error"|"violations": ..., "caps": {...}}`` — hard rail
    violations (single-order / daily notional caps, duplicates) are returned
    without creating an intent so the agent can resize and retry.
    """
    side = (side or "").strip().lower()
    order_type = (order_type or "market").strip().lower()
    account_kind = (account_kind or "").strip().lower()
    rationale = (rationale or "").strip()
    risk = ConfigStore(db).get_risk()
    caps = {
        "max_single_order_notional_gbp": float(risk["max_single_order_notional"]),
        "max_daily_notional_gbp": float(risk["max_daily_notional"]),
        "executed_today_gbp": round(_daily_executed_notional(db), 2),
    }

    def _fail(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message, "caps": caps}

    if side not in _SIDES:
        return _fail("side must be 'buy' or 'sell'")
    if order_type not in _ORDER_TYPES:
        return _fail(f"order_type must be one of {sorted(_ORDER_TYPES)}")
    # Crypto routes to the connected exchange (crypto_service); same rails + cap.
    from app.services import crypto_service

    if account_kind == "crypto" or (not account_kind and crypto_service.is_crypto_symbol(symbol or instrument_code)):
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
    }
    if hard:
        return {"ok": False, "violations": hard, "sizing": sizing, "caps": caps}

    meta: dict[str, Any] = {
        "source": (source or "agent")[:64],
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
    if warnings:
        meta["warnings"] = warnings

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
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
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
        "rationale": intent.rationale,
        "failure_reason": intent.failure_reason,
    }


def list_proposals(db: Session, *, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
    expire_stale_intents(db)
    q = select(TradeIntent).order_by(desc(TradeIntent.created_at)).limit(max(1, min(int(limit or 20), 100)))
    if status and status != "all":
        q = q.where(TradeIntent.status == status)
    return [serialize_intent(i) for i in db.execute(q).scalars().all()]
