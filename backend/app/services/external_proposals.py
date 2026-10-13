"""Trade ideas for accounts OpenPF can read but not trade.

Archie can analyse every connected account, so it can also suggest a trade in
one (trim MSFT in IBKR, sell SOL on a read-only Binance key). Those become an
ordinary ``proposed`` TradeIntent marked ``read_only`` with the broker to use;
``execute_intent`` refuses them (``broker_router.refusal``) and the UI shows
"Place it in <broker>" instead of Approve.
"""

from __future__ import annotations

import math
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.entities import AccountSnapshot, PositionSnapshot, TradeIntent
from app.services.config_store import ConfigStore

_SUFFIX_CCY = {".L": "GBX", ".DE": "EUR", ".F": "EUR", ".AS": "EUR", ".PA": "EUR", ".MI": "EUR", ".MC": "EUR",
               ".SW": "CHF", ".TO": "CAD", ".HK": "HKD", ".AX": "AUD"}


def _guess_currency(yf: str) -> str:
    up = yf.upper()
    if up.endswith("-USD"):
        return "USD"
    if up.endswith("-GBP"):
        return "GBP"
    for suffix, ccy in _SUFFIX_CCY.items():
        if up.endswith(suffix):
            return ccy
    return "USD"


def _held(db: Session, account_kind: str, symbol: str) -> PositionSnapshot | None:
    ts = db.execute(select(AccountSnapshot.fetched_at).where(AccountSnapshot.account_kind == account_kind)
                    .order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    if ts is None:
        return None
    sym = symbol.strip().upper()
    for p in db.execute(select(PositionSnapshot).where(PositionSnapshot.account_kind == account_kind,
                                                       PositionSnapshot.fetched_at == ts)).scalars():
        if sym in {(p.ticker or "").upper(), (p.instrument_code or "").upper()}:
            return p
    return None


def propose_readonly_trade(
    db: Session, *, account_kind: str, side: str, rationale: str, symbol: str | None, instrument_code: str | None,
    quantity: float | None, notional_gbp: float | None, order_type: str, limit_price: float | None,
    play_id: str | None, source: str, caps: dict[str, Any],
) -> dict[str, Any]:
    from app.services.connections_service import account_info
    from app.services.crypto_exchanges import CRYPTO_ASSETS, asset_from_symbol, is_crypto_account
    from app.services.fx import get_fx_rate
    from app.services.proposal_service import serialize_intent

    def _fail(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message, "caps": caps}

    store = ConfigStore(db)
    info = account_info(store, account_kind)
    if info is None:
        return _fail(f"unknown account '{account_kind}' — use an account_kind from the portfolio's accounts")
    if not rationale:
        return _fail("rationale is required — say why, and what would invalidate the idea")
    sym = (instrument_code or symbol or "").strip()
    if not sym:
        return _fail("pass symbol or instrument_code")
    if (quantity is None) == (notional_gbp is None):
        return _fail("pass exactly one of quantity or notional_gbp")
    kind = info["account_kind"]
    crypto = is_crypto_account(kind)
    held = _held(db, kind, asset_from_symbol(sym) if crypto else sym)
    if held is not None and held.current_price:
        price, currency, ticker = float(held.current_price), (held.currency or "USD"), held.ticker
    else:
        from app.services.market_data import fetch_history

        if crypto:
            asset = asset_from_symbol(sym)
            if asset not in CRYPTO_ASSETS:
                return _fail(f"{asset!r} isn't a supported crypto asset")
            yf, ticker = f"{asset}-GBP", asset
        else:
            yf, ticker = sym.upper(), sym.upper().split(".")[0]
        try:
            price = float(fetch_history(yf, lookback_days=10)["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            return _fail(f"could not price {sym!r} — pass the market ticker (e.g. VWRL.L, MSFT)")
        currency = "GBP" if crypto else _guess_currency(yf)
    if limit_price:
        price = float(limit_price)
    try:
        price_gbp = price * get_fx_rate(currency, "GBP")
    except Exception:  # noqa: BLE001
        return _fail(f"could not convert {currency} to GBP")
    if not math.isfinite(price_gbp) or price_gbp <= 0:
        return _fail(f"could not price {sym!r}")
    qty = float(quantity) if quantity is not None else math.floor(float(notional_gbp) / price_gbp * 1e6) / 1e6
    if qty <= 0:
        return _fail("that sizes to zero — check the amount")
    if side == "sell" and held is not None and qty > float(held.quantity or 0) + 1e-6:
        return {"ok": False, "violations": [f"position-guard: selling {qty:g} but only {held.quantity:g} held in "
                                            f"{info['label']}"], "caps": caps}
    estimated = round(qty * price_gbp, 2)
    meta: dict[str, Any] = {
        "source": (source or "agent")[:64], "account_kind": kind, "reference_price": price, "currency": currency,
        "price_gbp": round(price_gbp, 6), "read_only": True, "place_in": info["broker"], "account_label": info["label"],
        "instrument_name": getattr(held, "ticker", None) or ticker,
    }
    if notional_gbp is not None:
        meta["requested_notional_gbp"] = notional_gbp
    if play_id and str(play_id).strip().lower() == "core":
        meta["owner"] = "core"
    elif play_id:
        from app.models.entities import Play

        play = db.get(Play, str(play_id).strip())
        if play is not None:
            meta["play_id"] = play.id
            meta["play_title"] = play.title
    intent = TradeIntent(
        status="proposed", broker_mode="external", symbol=ticker[:32], instrument_code=(sym.upper())[:64], side=side,
        order_type=order_type if order_type in {"market", "limit"} else "market", quantity=qty,
        limit_price=float(limit_price) if limit_price else None, estimated_notional=estimated,
        rationale=(rationale or "")[:8000], meta=meta,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    note = (f"Read-only account: OpenPF can't place this. It's recorded as a suggestion — the user places it "
            f"themselves in {info['broker']}.")
    return {"ok": True, "intent": serialize_intent(intent), "warnings": [note], "caps": caps, "read_only": True,
            "place_in": info["broker"], "note": note}
