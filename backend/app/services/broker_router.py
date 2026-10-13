"""Broker routing for ``execution_service.execute_intent``.

``execute_intent`` stays the single gate: it re-checks every rail (including
the crypto cap), atomically claims the intent and only then asks this module
to place the order. Trading 212 accounts keep their existing in-line path;
crypto intents are routed here to the connected exchange that holds them.

``refusal`` is the capability check: an account from a read-only connection
(IBKR, Alpaca, SnapTrade, a query-only crypto key) can never execute — the
user is told to place the order in that broker instead.

Nothing in this module is reachable from an agent process: there is no MCP
tool for it, and exchange keys exist only in the backend's ConfigStore.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.entities import TradeIntent
from app.services.crypto_exchanges import CRYPTO_ACCOUNT, asset_from_symbol, is_crypto_account

ACCOUNTS = ("invest", "stocks_isa", CRYPTO_ACCOUNT)


def is_crypto(account_kind: str | None) -> bool:
    return is_crypto_account(account_kind)


def refusal(db: Session, account_kind: str | None) -> str | None:
    """Why ``account_kind`` can't execute (None → it may). Read-only and unknown
    accounts are refused here, before any rail or broker call."""
    from app.services.config_store import ConfigStore
    from app.services.connections_service import trade_refusal

    kind = str(account_kind or "").strip().lower()
    if kind in ACCOUNTS[:2]:
        return None
    if not kind:
        return "no account on this intent"
    return trade_refusal(ConfigStore(db), kind)


def refusal_for(db: Session, intent: TradeIntent, account_kind: str | None = None) -> str | None:
    """``refusal`` for an intent: its own account, an override, and the
    ``read_only`` mark a read-only suggestion carries — all must pass."""
    meta = intent.meta or {}
    proposed = meta.get("account_kind")
    if meta.get("read_only"):
        broker = meta.get("place_in") or "your broker"
        return (f"read-only: this is a suggestion for {meta.get('account_label') or proposed}, which OpenPF can only "
                f"read. Place it yourself in {broker}.")
    for kind in dict.fromkeys(k for k in (proposed, account_kind) if k):
        why = refusal(db, kind)
        if why:
            return why
    return None


def assert_sandbox_safe(config: Any, account_kind: str | None = None) -> None:
    """Crypto counterpart of the T212 sandbox probe in execute_intent: a sandbox
    instance may only ever reach the in-process mock exchange."""
    from app.core.sandbox import SandboxViolation, is_sandbox
    from app.services import crypto_service

    if not is_sandbox():
        return
    client = crypto_service.client_for(config, account_kind)
    if client is not None and not client.sandbox:
        raise SandboxViolation("sandbox mode: refusing to execute against a real crypto exchange")


def execute_crypto(db: Session, intent: TradeIntent, *, mode: str,
                   log_event: Callable[..., None], error_cls: type[Exception]) -> TradeIntent:
    """Place an already-claimed (status 'executing') crypto intent.

    Raises ``error_cls`` (ExecutionError) for definitely-not-sent failures so
    execute_intent's handler returns the intent to 'approved' for a retry."""
    from app.services import crypto_service
    from app.services.config_store import ConfigStore
    from app.services.crypto_client import CryptoAuthError, CryptoError, CryptoNotPlaced

    config = ConfigStore(db)
    asset = asset_from_symbol(intent.symbol or intent.instrument_code)
    meta = intent.meta or {}
    account = crypto_service.resolve_account(config, meta.get("account_kind"))

    if mode == "paper":
        fill = float((meta.get("crypto") or {}).get("price") or meta.get("reference_price") or 0.0)
        client = crypto_service.client_for(config, account)
        if client is not None:
            try:
                t = client.fetch_ticker(asset)
                fill = float((t.get("ask") if intent.side == "buy" else t.get("bid")) or t.get("last") or fill)
            except Exception:  # noqa: BLE001 — paper fill falls back to the proposal's price
                pass
        intent.execution_price = fill
        intent.executed_at = datetime.utcnow()
        intent.status = "executed"
        intent.broker_order_id = f"paper-{intent.id[:8]}"
        db.add(intent)
        db.commit()
        db.refresh(intent)
        log_event(db, intent.id, "info", "paper execution completed (crypto)", {"fill_price": fill, "asset": asset})
        return intent

    assert_sandbox_safe(config, account)
    cfg = crypto_service.get_config(config, account)
    if not crypto_service.tradable(cfg):
        raise error_cls("this crypto connection is read-only — press Enable trading on it in Settings → Accounts and add a trade key")
    client = crypto_service.client_for(config, account)
    if client is None:
        raise error_cls("no verified trade-only crypto exchange key is connected (Settings → Accounts)")
    qty = float(intent.quantity or 0.0)
    if not math.isfinite(qty) or qty <= 0:
        raise error_cls("invalid crypto quantity")
    try:
        order = client.place_market_order(asset, intent.side, qty, reference_price=float(meta.get("reference_price") or 0) or None)
    except (CryptoNotPlaced, CryptoAuthError) as exc:
        raise error_cls(f"{client.label}: order not placed — {exc}") from exc
    except CryptoError as exc:
        # Sent but no clear answer (timeout, 5xx): never invite a blind retry.
        raise CryptoError(f"{exc} — outcome unknown: the order may or may not have reached {client.label}. "
                          f"Check your {client.label} orders before proposing it again.") from exc

    intent.executed_at = datetime.utcnow()
    intent.status = "executed"
    intent.broker_order_id = str(order.get("id") or f"crypto-{intent.id[:8]}")
    avg = order.get("average") or order.get("price")
    if avg:
        intent.execution_price = float(avg)
    fee = order.get("fee") or {}
    intent.meta = {**meta, "crypto_fill": {"exchange": client.exchange_id, "status": order.get("status"),
                                            "filled": order.get("filled"), "cost": order.get("cost"),
                                            "fee": fee.get("cost"), "fee_currency": fee.get("currency")}}
    db.add(intent)
    db.commit()
    db.refresh(intent)
    log_event(db, intent.id, "info", "crypto order accepted",
              {"exchange": client.exchange_id, "order_id": intent.broker_order_id, "status": order.get("status")})
    return intent
