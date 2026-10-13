"""Mock crypto exchange for sandbox mode (``OPENPF_SANDBOX=1``).

An in-process stand-in for a CCXT exchange: no network to any exchange host,
fake keys only. Prices are real where possible (Yahoo ``BTC-GBP`` etc. via
``market_data``) and fall back to fixtures offline. Balances, fills and the
deposit history persist to a small JSON file next to the sandbox broker state,
so a demo order sticks.

Keys it accepts (mirroring how a real key's permissions work):

* ``sandbox-crypto`` — trade-only (view + trade, no withdraw). The demo key.
* ``sandbox-crypto-withdraw`` — can withdraw. Exists so the "refuse keys with
  withdrawal permission" path can be demonstrated and tested end to end.
* ``sandbox-crypto-read`` — query-only (view, no trade, no withdraw): the key a
  read-only connection uses.

The mock Kraken account above is the one that trades. Other exchanges connected
in a sandbox (Binance, Coinbase) are read-only fixtures with small fixed
balances (``FIXTURE_BALANCES``), so a demo can show several exchanges at once.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.crypto_exchanges import CRYPTO_ASSETS, EXCHANGES, SANDBOX_EXCHANGE, asset_from_symbol, yfinance_symbol

logger = logging.getLogger(__name__)

SANDBOX_KEY = "sandbox-crypto"
SANDBOX_SECRET = "sandbox"
SANDBOX_WITHDRAW_KEY = "sandbox-crypto-withdraw"
SANDBOX_READ_KEY = "sandbox-crypto-read"
SANDBOX_CRYPTO_CREDENTIALS = {"api_key": SANDBOX_KEY, "api_secret": SANDBOX_SECRET}
SANDBOX_READ_CREDENTIALS = {"api_key": SANDBOX_READ_KEY, "api_secret": SANDBOX_SECRET}

# Read-only fixture accounts for the other exchanges: coins + quote cash, with an
# average cost as a multiple of today's price.
FIXTURE_BALANCES: dict[str, dict[str, Any]] = {
    "binance": {"balances": {"USDC": 85.0, "SOL": 1.8, "ETH": 0.05}, "cost_mult": {"SOL": 0.82, "ETH": 1.06}},
    "coinbase": {"balances": {"GBP": 40.0, "BTC": 0.0015, "LINK": 6.0}, "cost_mult": {"BTC": 0.71, "LINK": 1.12}},
    "kraken": {"balances": {"GBP": 25.0, "XRP": 120.0}, "cost_mult": {"XRP": 0.64}},
}

# Offline fixtures (GBP). Only used when no market data is reachable.
FIXTURE_PRICES_GBP = {"BTC": 62000.0, "ETH": 2300.0, "SOL": 140.0, "XRP": 1.9, "ADA": 0.55,
                      "LINK": 13.0, "LTC": 70.0, "DOT": 3.2}

# Generous fake cash, and it tops itself up (``top_up``): a demo should never
# block on money that doesn't exist.
SEED_CASH_GBP = 2500.0
TOP_UP_STEP_GBP = 500.0
# Two small starter-DCA fills: (asset, quantity, days ago, price multiple of today's)
SEED_FILLS: list[tuple[str, float, int, float]] = [("BTC", 0.0009, 21, 0.97), ("BTC", 0.0009, 14, 1.02),
                                                    ("ETH", 0.012, 14, 0.95)]

_lock = threading.RLock()
_price_cache: dict[str, tuple[float, float]] = {}
_PRICE_TTL = 300.0


def _state_path() -> Path:
    raw = os.environ.get("OPENPF_SANDBOX_STATE", "").strip()
    base = Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "sandbox_state.json"
    return base.with_name(base.stem + ".crypto.json")


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def price_gbp(asset: str) -> float:
    """Latest GBP price (real when reachable, cached a few minutes; fixture offline)."""
    asset = asset_from_symbol(asset)
    now = time.time()
    hit = _price_cache.get(asset)
    if hit and now - hit[1] < _PRICE_TTL:
        return hit[0]
    value = hit[0] if hit else FIXTURE_PRICES_GBP.get(asset, 100.0)
    if os.environ.get("OPENPF_CRYPTO_OFFLINE", "").strip() not in {"1", "true"}:
        try:
            from app.services.market_data import fetch_history

            latest = float(fetch_history(yfinance_symbol(asset, "GBP"), lookback_days=10)["close"].iloc[-1])
            if math.isfinite(latest) and latest > 0:
                value = latest
        except Exception:  # noqa: BLE001 — offline / throttled: keep the fixture
            pass
    _price_cache[asset] = (value, now)
    return value


def _seed_state() -> dict[str, Any]:
    now = datetime.utcnow()
    state: dict[str, Any] = {"seeded_at": _iso(now), "balances": {"GBP": SEED_CASH_GBP}, "cost": {},
                             "orders": [], "deposits": []}
    spent = 0.0
    first = now - timedelta(days=max(d for _, _, d, _ in SEED_FILLS) + 1)
    for asset, qty, days_ago, mult in SEED_FILLS:
        px = round(price_gbp(asset) * mult, 2)
        when = now - timedelta(days=days_ago)
        cost = qty * px
        fee = round(cost * EXCHANGES[SANDBOX_EXCHANGE]["fees"]["taker"], 4)
        spent += cost + fee
        _apply_fill(state, asset, "buy", qty, px)
        state["orders"].append(_order(asset, "buy", qty, px, fee, when))
    state["deposits"].append({"id": "sbx-crypto-dep-1", "currency": "GBP", "amount": round(spent + SEED_CASH_GBP, 2),
                              "timestamp": _iso(first), "status": "ok"})
    return state


def _apply_fill(state: dict[str, Any], asset: str, side: str, qty: float, px: float) -> None:
    bal = state["balances"]
    held = float(bal.get(asset, 0.0))
    cost = state["cost"].setdefault(asset, {"qty": 0.0, "avg": 0.0})
    if side == "buy":
        new_qty = held + qty
        cost["avg"] = ((cost["avg"] * held) + px * qty) / new_qty if new_qty else 0.0
        bal[asset] = round(new_qty, 10)
    else:
        bal[asset] = round(max(held - qty, 0.0), 10)
    cost["qty"] = bal[asset]


def _order(asset: str, side: str, qty: float, px: float, fee: float, when: datetime) -> dict[str, Any]:
    quote = EXCHANGES[SANDBOX_EXCHANGE]["quote"]
    return {"id": f"SBX-{uuid.uuid4().hex[:10].upper()}", "symbol": f"{asset}/{quote}", "side": side, "type": "market",
            "status": "closed", "amount": qty, "filled": qty, "average": px, "price": px, "cost": round(qty * px, 2),
            "fee": {"currency": quote, "cost": fee}, "datetime": _iso(when)}


def _load() -> dict[str, Any]:
    path = _state_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("sandbox crypto state unreadable; reseeding", exc_info=True)
    state = _seed_state()
    _save(state)
    return state


def _save(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def reset() -> None:
    with _lock:
        _save(_seed_state())


class SandboxExchangeError(RuntimeError):
    pass


def check_key(api_key: str, api_secret: str) -> dict[str, Any]:
    """Permissions for a sandbox key (mirrors a real exchange's key-permission API)."""
    if api_key == SANDBOX_KEY and api_secret == SANDBOX_SECRET:
        return {"can_view": True, "can_trade": True, "can_withdraw": False}
    if api_key == SANDBOX_WITHDRAW_KEY:
        return {"can_view": True, "can_trade": True, "can_withdraw": True}
    if api_key == SANDBOX_READ_KEY and api_secret == SANDBOX_SECRET:
        return {"can_view": True, "can_trade": False, "can_withdraw": False}
    raise SandboxExchangeError("invalid sandbox crypto key")


def balances() -> dict[str, float]:
    with _lock:
        return {k: float(v) for k, v in _load()["balances"].items() if float(v) > 0}


def cost_basis() -> dict[str, float]:
    with _lock:
        return {k: float(v.get("avg") or 0.0) for k, v in (_load().get("cost") or {}).items()}


def deposits() -> list[dict[str, Any]]:
    with _lock:
        return list(_load().get("deposits") or [])


def _top_up_locked(state: dict[str, Any], needed_gbp: float) -> float:
    bal = state["balances"]
    have = float(bal.get("GBP", 0.0))
    if needed_gbp <= have + 1e-9:
        return 0.0
    # Round up to whole steps, with a step of headroom for the next demo order.
    added = math.ceil((needed_gbp - have) / TOP_UP_STEP_GBP) * TOP_UP_STEP_GBP + TOP_UP_STEP_GBP
    bal["GBP"] = round(have + added, 4)
    state.setdefault("deposits", []).append({
        "id": f"sbx-crypto-topup-{uuid.uuid4().hex[:8]}", "currency": "GBP", "amount": added,
        "timestamp": _iso(datetime.utcnow()), "status": "ok", "note": "demo cash added",
    })
    logger.info("sandbox crypto: demo cash added £%.0f", added)
    return added


def top_up(needed_gbp: float) -> float:
    """Make sure the fake GBP balance covers ``needed_gbp``; returns what was added
    (recorded as a deposit, so returns stay honest)."""
    with _lock:
        state = _load()
        added = _top_up_locked(state, needed_gbp)
        if added:
            _save(state)
        return added


def ticker(asset: str) -> dict[str, float]:
    px = price_gbp(asset)
    spread = px * 0.0004  # a tight GBP book for a liquid pair
    return {"bid": px - spread / 2, "ask": px + spread / 2, "last": px}


def place_market_order(asset: str, side: str, amount: float) -> dict[str, Any]:
    asset = asset_from_symbol(asset)
    if asset not in CRYPTO_ASSETS:
        raise SandboxExchangeError(f"unknown market {asset}")
    if side not in {"buy", "sell"} or not (math.isfinite(amount) and amount > 0):
        raise SandboxExchangeError("invalid order")
    t = ticker(asset)
    px = t["ask"] if side == "buy" else t["bid"]
    fee_rate = EXCHANGES[SANDBOX_EXCHANGE]["fees"]["taker"]
    with _lock:
        state = _load()
        bal = state["balances"]
        cost = amount * px
        fee = round(cost * fee_rate, 4)
        if side == "buy":
            _top_up_locked(state, cost + fee)  # fake money: never refuse a demo buy for cash
            bal["GBP"] = round(float(bal.get("GBP", 0.0)) - cost - fee, 4)
        else:
            if amount > float(bal.get(asset, 0.0)) + 1e-10:
                raise SandboxExchangeError("EOrder:Insufficient funds")
            bal["GBP"] = round(float(bal.get("GBP", 0.0)) + cost - fee, 4)
        _apply_fill(state, asset, side, amount, px)
        order = _order(asset, side, amount, px, fee, datetime.utcnow())
        state["orders"].insert(0, order)
        _save(state)
        return order


def fetch_order(order_id: str) -> dict[str, Any] | None:
    with _lock:
        return next((o for o in _load()["orders"] if o["id"] == order_id), None)
