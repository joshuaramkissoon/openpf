"""Broker routing for ``execution_service.execute_intent``.

``execute_intent`` stays the single gate: it re-checks every rail (including
the crypto cap), atomically claims the intent and only then asks this module
to place the order. Trading 212 accounts keep their existing in-line path;
crypto intents are routed here to the connected exchange.

Nothing in this module is reachable from an agent process: there is no MCP
tool for it, and exchange keys exist only in the backend's ConfigStore.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.entities import TradeIntent
from app.services.crypto_exchanges import CRYPTO_ACCOUNT, asset_from_symbol

ACCOUNTS = ("invest", "stocks_isa", CRYPTO_ACCOUNT)


def is_crypto(account_kind: str | None) -> bool:
    return account_kind == CRYPTO_ACCOUNT


def assert_sandbox_safe(config: Any) -> None:
    """Crypto counterpart of the T212 sandbox probe in execute_intent: a sandbox
    instance may only ever reach the in-process mock exchange."""
    from app.core.sandbox import SandboxViolation, is_sandbox
    from app.services import crypto_service

    if not is_sandbox():
        return
    client = crypto_service.client_for(config)
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

    if mode == "paper":
        fill = float((meta.get("crypto") or {}).get("price") or meta.get("reference_price") or 0.0)
        client = crypto_service.client_for(config)
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

    assert_sandbox_safe(config)
    client = crypto_service.client_for(config)
    if client is None:
        raise error_cls("no verified trade-only crypto exchange key is connected (Settings → Crypto)")
    if not crypto_service.get_config(config).get("verified", {}).get("can_trade", True):
        raise error_cls("the connected crypto key can't trade — add the Create & modify orders permission")
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
