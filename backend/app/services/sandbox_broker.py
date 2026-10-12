"""Sandbox broker: a local, in-process stand-in for the Trading 212 API.

Point the app at it with ``T212_BASE_ENV=sandbox`` (or ``OPENPF_SANDBOX=1``,
which also seeds sandbox credentials into a fresh database). Every T212 call the
app makes — dashboard reads, Archie's MCP tools, order execution — then hits
``/sandbox-t212/api/v0`` on this same server instead of trading212.com, so the
whole product works end-to-end with no broker account and no real money.

Prices are real (yfinance via ``market_data``, with its own fallbacks); the
portfolio, cash, fills and order history are a deterministic fake seeded on
first use and persisted to a small JSON file so orders you place stick.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

SANDBOX_PREFIX = "/sandbox-t212/api/v0"
ACCOUNTS = ("invest", "stocks_isa")

# Credentials the sandbox accepts. Read keys can only read; exec keys can trade —
# mirroring the read/execute key split the app uses against the real broker.
SANDBOX_CREDENTIALS = {
    "invest": {"t212_api_key": "sandbox-invest", "t212_api_secret": "sandbox", "exec_api_key": "sandbox-invest-exec", "exec_api_secret": "sandbox"},
    "stocks_isa": {"t212_api_key": "sandbox-isa", "t212_api_secret": "sandbox", "exec_api_key": "sandbox-isa-exec", "exec_api_secret": "sandbox"},
}
_KEY_TO_ACCOUNT = {
    "sandbox-invest": ("invest", "read"),
    "sandbox-invest-exec": ("invest", "execute"),
    "sandbox-isa": ("stocks_isa", "read"),
    "sandbox-isa-exec": ("stocks_isa", "execute"),
}

# (ticker, name, approx price USD used only if no market data is reachable)
UNIVERSE: list[tuple[str, str, float]] = [
    ("NVDA", "NVIDIA", 180.0), ("MSFT", "Microsoft", 500.0), ("AAPL", "Apple", 240.0),
    ("GOOGL", "Alphabet (Class A)", 240.0), ("AMZN", "Amazon", 225.0), ("META", "Meta Platforms", 720.0),
    ("TSM", "Taiwan Semiconductor (ADR)", 290.0), ("ASML", "ASML Holding (ADR)", 950.0),
    ("AVGO", "Broadcom", 340.0), ("AMD", "Advanced Micro Devices", 210.0), ("MU", "Micron Technology", 190.0),
    ("PLTR", "Palantir Technologies", 180.0), ("TSLA", "Tesla", 430.0), ("NET", "Cloudflare", 220.0),
    ("SHOP", "Shopify", 150.0), ("COIN", "Coinbase Global", 340.0), ("CCJ", "Cameco", 85.0),
    ("OKLO", "Oklo", 120.0), ("SMR", "NuScale Power", 38.0), ("RKLB", "Rocket Lab", 55.0),
    ("ISRG", "Intuitive Surgical", 460.0), ("LLY", "Eli Lilly", 800.0), ("NVO", "Novo Nordisk (ADR)", 55.0),
    ("V", "Visa", 345.0), ("MA", "Mastercard", 570.0), ("JPM", "JPMorgan Chase", 305.0),
    ("BRK.B", "Berkshire Hathaway (Class B)", 495.0), ("COST", "Costco", 930.0), ("KO", "Coca-Cola", 68.0),
    ("XOM", "Exxon Mobil", 115.0), ("NEE", "NextEra Energy", 75.0), ("VST", "Vistra", 200.0),
    ("CEG", "Constellation Energy", 330.0), ("ETN", "Eaton", 370.0), ("ANET", "Arista Networks", 145.0),
    ("CRWD", "CrowdStrike", 480.0), ("SNOW", "Snowflake", 230.0), ("UBER", "Uber", 95.0),
    ("NFLX", "Netflix", 1200.0), ("DIS", "Walt Disney", 112.0), ("IONQ", "IonQ", 65.0),
    ("RGTI", "Rigetti Computing", 30.0), ("ARM", "Arm Holdings (ADR)", 160.0), ("SOFI", "SoFi Technologies", 27.0),
    ("HOOD", "Robinhood Markets", 140.0), ("UUUU", "Energy Fuels", 15.0), ("LEU", "Centrus Energy", 280.0),
    ("SPY", "SPDR S&P 500 ETF", 665.0), ("QQQ", "Invesco QQQ", 600.0),
]

# The seeded book: (account, ticker, quantity, cost basis as a multiple of the price ~1y ago)
SEED_POSITIONS: list[tuple[str, str, float]] = [
    ("stocks_isa", "NVDA", 62.0), ("stocks_isa", "MSFT", 14.0), ("stocks_isa", "GOOGL", 24.0),
    ("stocks_isa", "AMZN", 21.0), ("stocks_isa", "ASML", 4.5), ("stocks_isa", "TSM", 16.0),
    ("stocks_isa", "META", 5.0), ("stocks_isa", "AAPL", 12.0), ("stocks_isa", "LLY", 3.0),
    ("stocks_isa", "V", 8.0), ("stocks_isa", "CCJ", 40.0), ("stocks_isa", "COST", 2.0),
    ("invest", "PLTR", 28.0), ("invest", "TSLA", 6.0), ("invest", "AMD", 12.0),
    ("invest", "MU", 10.0), ("invest", "NET", 9.0), ("invest", "RKLB", 45.0),
    ("invest", "OKLO", 14.0), ("invest", "COIN", 4.0),
]
SEED_CASH_GBP = {"invest": 1840.0, "stocks_isa": 3215.0}
SEED_DEPOSITS_GBP = {"invest": 16000.0, "stocks_isa": 40000.0}

_lock = threading.RLock()
_price_cache: dict[str, tuple[float, float]] = {}
_PRICE_TTL = 300.0


def code_for(ticker: str) -> str:
    return f"{ticker.replace('.', '_')}_US_EQ" if "." in ticker else f"{ticker}_US_EQ"


def _ticker_for_code(code: str) -> str:
    base = code.removesuffix("_US_EQ")
    return base.replace("_", ".") if base.endswith("_B") else base


_NAMES = {code_for(t): n for t, n, _ in UNIVERSE}
_FALLBACK = {code_for(t): p for t, _, p in UNIVERSE}


def is_sandbox_enabled_env() -> bool:
    return os.environ.get("OPENPF_SANDBOX", "").strip().lower() in {"1", "true", "yes", "on"}


def sandbox_base_url() -> str:
    return os.environ.get("T212_SANDBOX_URL", "").strip() or f"http://127.0.0.1:{os.environ.get('PORT', '8000')}{SANDBOX_PREFIX}"


def _state_path() -> Path:
    raw = os.environ.get("OPENPF_SANDBOX_STATE", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path(__file__).resolve().parents[2] / "sandbox_state.json"


# ── prices ────────────────────────────────────────────────────────────────────

def _history(ticker: str, days: int):
    from app.services.market_data import fetch_history

    return fetch_history(ticker.replace(".", "-"), lookback_days=days)


def price(code: str) -> float:
    """Latest price in instrument currency (USD), cached for a few minutes."""
    now = time.time()
    hit = _price_cache.get(code)
    if hit and now - hit[1] < _PRICE_TTL:
        return hit[0]
    value = _FALLBACK.get(code, 100.0)
    try:
        frame = _history(_ticker_for_code(code), 10)
        value = float(frame["close"].iloc[-1])
    except Exception:  # noqa: BLE001 — offline / throttled: keep the fallback
        if hit:
            value = hit[0]
    _price_cache[code] = (value, now)
    return value


def _price_on(code: str, on: date) -> float:
    try:
        frame = _history(_ticker_for_code(code), 420)
        dates = frame["date"] if "date" in frame.columns else frame.index
        best = None
        for i, d in enumerate(dates):
            day = d.date() if hasattr(d, "date") else date.fromisoformat(str(d)[:10])
            if day <= on:
                best = i
        if best is not None:
            return float(frame["close"].iloc[best])
    except Exception:  # noqa: BLE001
        pass
    return price(code) * 0.8


def gbpusd() -> float:
    hit = _price_cache.get("GBPUSD")
    if hit and time.time() - hit[1] < _PRICE_TTL:
        return hit[0]
    value = 1.33
    try:
        from app.services.fx import get_fx_rate

        value = float(get_fx_rate("GBP", "USD")) or value
    except Exception:  # noqa: BLE001
        pass
    _price_cache["GBPUSD"] = (value, time.time())
    return value


# ── state ─────────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _rand(seed: str) -> float:
    return int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def _seed_state() -> dict[str, Any]:
    today = date.today()
    fx = gbpusd()
    state: dict[str, Any] = {"seeded_at": _iso(datetime.utcnow()), "accounts": {}}
    for account in ACCOUNTS:
        state["accounts"][account] = {
            "cash": SEED_CASH_GBP[account],
            "positions": {},
            "orders": [],
            "pending": [],
            "dividends": [],
            "transactions": [],
        }
    for account in ACCOUNTS:
        acct = state["accounts"][account]
        opened = datetime.combine(today - timedelta(days=400), datetime.min.time()) + timedelta(hours=9)
        acct["transactions"].append({
            "type": "DEPOSIT", "amount": SEED_DEPOSITS_GBP[account], "currency": "GBP",
            "dateTime": _iso(opened), "reference": f"sbx-dep-{account}",
        })
    for account, ticker, qty in SEED_POSITIONS:
        code = code_for(ticker)
        acct = state["accounts"][account]
        # Two buys per name at deterministic dates over the last ~13 months.
        d1 = today - timedelta(days=int(120 + _rand(code + "a") * 260))
        d2 = today - timedelta(days=int(20 + _rand(code + "b") * 90))
        q1 = round(qty * (0.55 + 0.2 * _rand(code + "q")), 4)
        total_cost_usd = 0.0
        for when, q in ((d1, q1), (d2, round(qty - q1, 4))):
            px = _price_on(code, when)
            total_cost_usd += px * q
            filled_at = datetime.combine(when, datetime.min.time()) + timedelta(hours=15, minutes=int(_rand(code + str(when)) * 50))
            acct["orders"].append(_order_record(code, "BUY", q, px, fx, filled_at, "MARKET"))
        acct["positions"][code] = {"quantity": qty, "avg_price": total_cost_usd / qty, "opened_at": _iso(datetime.combine(d1, datetime.min.time()))}
    for account in ACCOUNTS:
        state["accounts"][account]["orders"].sort(key=lambda o: o["order"]["createdAt"], reverse=True)
    # A couple of dividends so income views have something to show.
    for account, ticker, amount, days_ago in (("stocks_isa", "MSFT", 9.12, 34), ("stocks_isa", "AAPL", 2.31, 58), ("stocks_isa", "V", 3.88, 75), ("stocks_isa", "COST", 2.02, 101), ("stocks_isa", "MSFT", 8.95, 125)):
        paid = datetime.combine(today - timedelta(days=days_ago), datetime.min.time()) + timedelta(hours=12)
        state["accounts"][account]["dividends"].append({
            "ticker": code_for(ticker), "amount": amount, "currency": "GBP", "paidOn": _iso(paid),
            "quantity": next((q for a, t, q in SEED_POSITIONS if a == account and t == ticker), 0.0),
            "reference": f"sbx-div-{ticker}-{days_ago}", "type": "ORDINARY",
        })
    return state


def _order_record(code: str, side: str, qty: float, px: float, fx: float, when: datetime, otype: str) -> dict[str, Any]:
    oid = int(_rand(f"{code}{when.isoformat()}{qty}") * 10**10) or uuid.uuid4().int % 10**10
    net_gbp = round(qty * px / fx, 2)
    return {
        "order": {
            "id": oid, "type": otype, "side": side, "status": "FILLED", "ticker": code,
            "instrument": {"ticker": code, "name": _NAMES.get(code, code), "currency": "USD", "isin": ""},
            "quantity": qty if side == "BUY" else -qty, "filledQuantity": qty, "filledValue": net_gbp,
            "currency": "GBP", "createdAt": _iso(when), "initiatedFrom": "API",
        },
        "fill": {
            "id": oid + 1, "quantity": qty if side == "BUY" else -qty, "price": round(px, 4), "filledAt": _iso(when), "type": "TRADE",
            "walletImpact": {"currency": "GBP", "netValue": net_gbp, "fxRate": round(1 / fx, 6), "taxes": []},
        },
    }


def _load() -> dict[str, Any]:
    path = _state_path()
    if path.is_file():
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("sandbox state unreadable; reseeding", exc_info=True)
    state = _seed_state()
    _save(state)
    logger.info("Seeded sandbox broker state at %s", path)
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


# ── API surface (shapes mirror the real T212 v0 API) ──────────────────────────

class SandboxError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def authenticate(header: str | None) -> tuple[str, str]:
    if not header or not header.startswith("Basic "):
        raise SandboxError(401, "missing credentials")
    try:
        key = base64.b64decode(header.split(" ", 1)[1]).decode().split(":", 1)[0]
    except Exception:  # noqa: BLE001
        raise SandboxError(401, "bad credentials") from None
    hit = _KEY_TO_ACCOUNT.get(key)
    if not hit:
        raise SandboxError(401, "unknown sandbox key")
    return hit


def _positions_payload(acct: dict[str, Any]) -> list[dict[str, Any]]:
    fx = gbpusd()
    out = []
    for code, pos in acct["positions"].items():
        qty = float(pos["quantity"])
        if qty <= 0:
            continue
        px = price(code)
        cost_gbp = qty * float(pos["avg_price"]) / fx
        value_gbp = qty * px / fx
        out.append({
            "instrument": {"ticker": code, "name": _NAMES.get(code, code), "currency": "USD", "isin": ""},
            "createdAt": pos.get("opened_at"),
            "quantity": qty, "quantityAvailableForTrading": qty, "quantityInPies": 0,
            "averagePricePaid": round(float(pos["avg_price"]), 4), "currentPrice": round(px, 4),
            "walletImpact": {
                "currency": "GBP", "totalCost": round(cost_gbp, 2), "currentValue": round(value_gbp, 2),
                "unrealizedProfitLoss": round(value_gbp - cost_gbp, 2), "fxImpact": 0.0,
            },
        })
    return sorted(out, key=lambda p: -p["walletImpact"]["currentValue"])


def account_summary(account: str) -> dict[str, Any]:
    with _lock:
        acct = _load()["accounts"][account]
        positions = _positions_payload(acct)
    invested_value = sum(p["walletImpact"]["currentValue"] for p in positions)
    cost = sum(p["walletImpact"]["totalCost"] for p in positions)
    reserved = sum(float(o.get("value") or 0) for o in acct["pending"] if o.get("side") == "BUY")
    return {
        "id": 10_000_000 + ACCOUNTS.index(account),
        "currency": "GBP",
        "totalValue": round(invested_value + acct["cash"], 2),
        "cash": {"availableToTrade": round(acct["cash"] - reserved, 2), "reservedForOrders": round(reserved, 2), "inPies": 0.0},
        "investments": {"currentValue": round(invested_value, 2), "totalCost": round(cost, 2), "unrealizedProfitLoss": round(invested_value - cost, 2), "realizedProfitLoss": 0.0},
    }


def positions(account: str) -> list[dict[str, Any]]:
    with _lock:
        return _positions_payload(_load()["accounts"][account])


def pending_orders(account: str) -> list[dict[str, Any]]:
    with _lock:
        return list(_load()["accounts"][account]["pending"])


def _page(items: list[dict[str, Any]], params: dict[str, str], path: str) -> dict[str, Any]:
    limit = max(1, min(int(params.get("limit") or 50), 50))
    start = int(params.get("cursor") or 0)
    chunk = items[start:start + limit]
    nxt = start + limit
    extra = "".join(f"&{k}={v}" for k, v in params.items() if k in {"ticker"} and v)
    return {"items": chunk, "nextPagePath": f"{SANDBOX_PREFIX}{path}?limit={limit}&cursor={nxt}{extra}" if nxt < len(items) else None}


def order_history(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["orders"])
    ticker = (params.get("ticker") or "").upper()
    if ticker:
        items = [i for i in items if i["order"]["ticker"].upper() == ticker]
    return _page(items, params, "/equity/history/orders")


def dividends(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["dividends"])
    return _page(items, params, "/equity/history/dividends")


def transactions(account: str, params: dict[str, str]) -> dict[str, Any]:
    with _lock:
        items = list(_load()["accounts"][account]["transactions"])
    return _page(items, params, "/equity/history/transactions")


def instruments() -> list[dict[str, Any]]:
    return [
        {"ticker": code_for(t), "name": n, "shortName": t, "currencyCode": "USD", "type": "ETF" if t in {"SPY", "QQQ"} else "STOCK",
         "isin": "", "workingScheduleId": 1, "minTradeQuantity": 0.01, "maxOpenQuantity": 100000, "addedOn": "2018-01-01T00:00:00.000Z"}
        for t, n, _ in UNIVERSE
    ]


def exchanges() -> list[dict[str, Any]]:
    return [{"id": 1, "name": "NASDAQ/NYSE (sandbox)", "workingSchedules": [{"id": 1, "timeEvents": []}]}]


def place_order(account: str, scope: str, kind: str, body: dict[str, Any]) -> dict[str, Any]:
    if scope != "execute":
        raise SandboxError(403, "read key cannot place orders")
    code = str(body.get("ticker") or "").upper()
    if code not in _NAMES:
        raise SandboxError(400, f"unknown instrument {code}")
    qty = float(body.get("quantity") or 0)
    if qty == 0:
        raise SandboxError(400, "quantity must be non-zero")
    side = "BUY" if qty > 0 else "SELL"
    now = datetime.utcnow()
    with _lock:
        state = _load()
        acct = state["accounts"][account]
        if kind != "market":
            oid = uuid.uuid4().int % 10**10
            pending = {
                "id": oid, "ticker": code, "type": kind.upper(), "side": side, "quantity": qty, "status": "NEW",
                "limitPrice": body.get("limitPrice"), "stopPrice": body.get("stopPrice"),
                "creationTime": _iso(now), "value": round(abs(qty) * price(code) / gbpusd(), 2),
            }
            acct["pending"].append(pending)
            _save(state)
            return pending
        px = price(code)
        fx = gbpusd()
        notional = abs(qty) * px / fx
        pos = acct["positions"].get(code, {"quantity": 0.0, "avg_price": px, "opened_at": _iso(now)})
        if side == "BUY":
            if notional > acct["cash"] + 1e-6:
                raise SandboxError(400, "insufficient funds")
            new_qty = pos["quantity"] + qty
            pos["avg_price"] = (pos["quantity"] * pos["avg_price"] + qty * px) / new_qty
            pos["quantity"] = round(new_qty, 6)
            acct["cash"] = round(acct["cash"] - notional, 2)
        else:
            if abs(qty) > pos["quantity"] + 1e-9:
                raise SandboxError(400, "selling more than held")
            pos["quantity"] = round(pos["quantity"] - abs(qty), 6)
            acct["cash"] = round(acct["cash"] + notional, 2)
        acct["positions"][code] = pos
        if pos["quantity"] <= 0:
            acct["positions"].pop(code, None)
        record = _order_record(code, side, abs(qty), px, fx, now, "MARKET")
        acct["orders"].insert(0, record)
        _save(state)
        order = dict(record["order"])
        order["filledPrice"] = round(px, 4)
        return order


def cancel_order(account: str, scope: str, order_id: str) -> None:
    if scope != "execute":
        raise SandboxError(403, "read key cannot cancel orders")
    with _lock:
        state = _load()
        acct = state["accounts"][account]
        before = len(acct["pending"])
        acct["pending"] = [o for o in acct["pending"] if str(o["id"]) != str(order_id)]
        if len(acct["pending"]) == before:
            raise SandboxError(404, "order not found")
        _save(state)


def seed_config_if_fresh(db) -> bool:
    """With OPENPF_SANDBOX=1, point a *fresh* database at the sandbox broker.

    Never touches a database that already has real credentials configured."""
    from app.services.config_store import ConfigStore

    store = ConfigStore(db)
    creds = store.get_credentials()
    has_real = any(
        str((creds.get(k) or {}).get("t212_api_key", "")).strip() and not str((creds.get(k) or {}).get("t212_api_key", "")).startswith("sandbox-")
        for k in ACCOUNTS
    )
    if has_real:
        logger.warning("OPENPF_SANDBOX=1 ignored: this database has real broker credentials")
        return False
    for account in ACCOUNTS:
        store.set_account_credentials(account, {**SANDBOX_CREDENTIALS[account], "enabled": True, "exec_enabled": True})
    store.set_broker({"t212_base_env": "sandbox", "broker_mode": "live"})
    return True
