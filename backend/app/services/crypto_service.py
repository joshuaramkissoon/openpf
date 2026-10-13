"""Crypto as a first-class part of the book.

* **Connection** — which exchange, its trade-only key (stored like the T212
  keys: never returned, never handed to an agent process) and the result of
  the withdrawal-permission check that gates it.
* **Snapshots** — balances → ``AccountSnapshot`` / ``PositionSnapshot`` rows
  under ``account_kind="crypto"``, so totals, Home's book-by-play, holdings,
  equity history and FX treat crypto like any other holding.
* **Proposals** — ``propose_trade`` delegates crypto symbols here: market
  orders in the exchange's quote currency (GBP or USDC), with fee and slippage
  estimates recorded on the intent for the proposal card.
* **Rails** — the crypto cap (max % of the book in crypto), applied by
  ``execution_service.rail_violations`` at proposal AND execution time.
* **Risk** — sleeve volatility and correlation with the rest of the book.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import AccountSnapshot, CashflowEvent, PositionSnapshot, TradeIntent
from app.services.config_store import ConfigStore
from app.services.crypto_client import CryptoAuthError, CryptoBroker, CryptoError, build_crypto_client
from app.services.crypto_exchanges import (
    CRYPTO_ACCOUNT,
    CRYPTO_ASSETS,
    EXCHANGES,
    SANDBOX_EXCHANGE,
    asset_from_symbol,
    is_crypto_symbol,
    market_symbol,
    quote_ccy,
    yfinance_symbol,
)

logger = logging.getLogger(__name__)

CONFIG_KEY = "crypto_config"
DEFAULT_MAX_CRYPTO_PCT = 0.05


class CryptoConnectError(ValueError):
    """Connecting a key was refused — the message is shown to the user as-is."""


# ── Connection / credentials ──────────────────────────────────────────────

def _fp(key: str) -> str:
    return hashlib.sha256((key or "").encode()).hexdigest()[:16]


def in_agent_process() -> bool:
    """True inside an MCP subprocess spawned for Archie (mcp_registry sets
    OPENPF_MCP_CONTEXT). Such a process never holds an exchange key in its
    config and never builds a keyed exchange client."""
    return bool(os.environ.get("OPENPF_MCP_CONTEXT", "").strip())


def _env_default() -> dict[str, Any]:
    s = get_settings()
    return {"exchange": (s.crypto_exchange or "").strip().lower(), "enabled": True,
            "api_key": (s.crypto_api_key or "").strip(), "api_secret": (s.crypto_api_secret or "").strip(),
            "verified": None, "fees": None}


def get_config(store: ConfigStore) -> dict[str, Any]:
    """The crypto connection INCLUDING secrets — backend use only, never serialise.

    Sandbox: the mock exchange's fake keys, always; stored and env keys ignored."""
    from app.core.sandbox import is_sandbox

    stored = dict(store.get(CONFIG_KEY, {}) or {})
    if is_sandbox():
        from app.services.crypto_sandbox import SANDBOX_CRYPTO_CREDENTIALS

        key = SANDBOX_CRYPTO_CREDENTIALS["api_key"]
        return {"exchange": SANDBOX_EXCHANGE, "enabled": bool(stored.get("enabled", True)),
                "api_key": key, "api_secret": SANDBOX_CRYPTO_CREDENTIALS["api_secret"],
                "verified": {"can_view": True, "can_trade": True, "can_withdraw": False, "method": "sandbox key",
                             "key_fp": _fp(key), "at": stored.get("seeded_at") or ""},
                "fees": None, "sandbox": True}
    cfg = {**_env_default(), **{k: v for k, v in stored.items() if v not in (None, "")}}
    cfg["sandbox"] = False
    if in_agent_process():
        # Agent-side processes learn only THAT a verified key exists.
        cfg["key_ok"] = bool(cfg.get("api_secret")) and _verified_ok(cfg)
        cfg["api_key"] = cfg["api_secret"] = ""
    return cfg


def _verified_ok(cfg: dict[str, Any]) -> bool:
    if "key_ok" in cfg:
        return bool(cfg["key_ok"])
    v = cfg.get("verified") or {}
    # The check must be for THIS key: swapping the key (DB/env) needs a re-check.
    return bool(v) and v.get("can_withdraw") is False and v.get("key_fp") == _fp(cfg.get("api_key", ""))


def is_connected(store: ConfigStore) -> bool:
    cfg = get_config(store)
    has_key = bool(cfg.get("api_key") and cfg.get("api_secret")) or bool(cfg.get("key_ok"))
    return bool(cfg.get("exchange") in EXCHANGES and has_key and cfg.get("enabled", True) and _verified_ok(cfg))


def client_for(store: ConfigStore) -> CryptoBroker | None:
    """The connected exchange's client, or None. Never builds one for an
    unverified key (one that could withdraw, or that was never checked)."""
    if in_agent_process() or not is_connected(store):
        return None
    cfg = get_config(store)
    return build_crypto_client(cfg["exchange"], cfg["api_key"], cfg["api_secret"])


def evaluate_permissions(perms: dict[str, Any]) -> list[str]:
    """Reasons to REFUSE a key (empty → acceptable)."""
    problems: list[str] = []
    if perms.get("can_withdraw") is True:
        problems.append("This key can withdraw or transfer funds. Create a new key with withdrawals switched OFF — "
                        "OpenPF only accepts trade-only keys.")
    elif perms.get("can_withdraw") is None:
        problems.append("Couldn't confirm that this key can't withdraw, so it was refused. Check the key settings "
                        "against the checklist and try again.")
    if perms.get("can_view") is False:
        problems.append("This key can't read balances — tick the Query/View funds permission.")
    if perms.get("leverage"):
        problems.append("This key has margin or futures enabled — switch those off; OpenPF only places spot orders.")
    return problems


def connect(store: ConfigStore, exchange: str, api_key: str, api_secret: str) -> dict[str, Any]:
    """Verify a key with the exchange and store it only if it is trade-only."""
    from app.core.sandbox import is_sandbox

    exchange = (exchange or "").strip().lower()
    if exchange not in EXCHANGES:
        raise CryptoConnectError("Pick Kraken, Coinbase or Binance.")
    key, secret = (api_key or "").strip(), (api_secret or "").replace("\\n", "\n").strip()
    if not key or not secret:
        raise CryptoConnectError("Paste both the API key and the secret.")
    if is_sandbox():
        from app.services.crypto_sandbox import SANDBOX_KEY, SANDBOX_WITHDRAW_KEY

        if key not in {SANDBOX_KEY, SANDBOX_WITHDRAW_KEY}:
            raise CryptoConnectError("Sandbox mode only accepts the sandbox keys (sandbox-crypto / sandbox).")
    client = build_crypto_client(exchange, key, secret)
    try:
        perms = client.key_permissions()
    except CryptoAuthError as exc:
        raise CryptoConnectError(f"{EXCHANGES[exchange]['label']} rejected that key: {exc}") from exc
    except CryptoError as exc:
        raise CryptoConnectError(f"Couldn't reach {EXCHANGES[exchange]['label']} to check the key: {exc}") from exc
    problems = evaluate_permissions(perms)
    if problems:
        logger.warning("crypto key refused on %s: %s", exchange, perms.get("detail"))
        raise CryptoConnectError(" ".join(problems))
    try:
        fees = client.trading_fees("BTC")
    except CryptoError:
        fees = None
    if is_sandbox():
        store.set(CONFIG_KEY, {**(store.get(CONFIG_KEY, {}) or {}), "enabled": True})
        return public(store)
    store.set(CONFIG_KEY, {
        "exchange": exchange, "enabled": True, "api_key": key, "api_secret": secret, "fees": fees,
        "verified": {"can_view": perms.get("can_view"), "can_trade": perms.get("can_trade"), "can_withdraw": False,
                     "method": perms.get("method"), "detail": str(perms.get("detail") or "")[:500],
                     "key_fp": _fp(key), "at": datetime.utcnow().isoformat()},
    })
    return public(store)


def disconnect(store: ConfigStore) -> dict[str, Any]:
    store.set(CONFIG_KEY, {"exchange": "", "enabled": False, "api_key": "", "api_secret": "", "verified": None, "fees": None})
    return public(store)


def set_enabled(store: ConfigStore, enabled: bool) -> dict[str, Any]:
    store.set(CONFIG_KEY, {**(store.get(CONFIG_KEY, {}) or {}), "enabled": bool(enabled)})
    return public(store)


def max_crypto_pct(risk: dict[str, Any]) -> float:
    try:
        v = float(risk.get("max_crypto_pct_of_book", DEFAULT_MAX_CRYPTO_PCT))
    except (TypeError, ValueError):
        v = DEFAULT_MAX_CRYPTO_PCT
    return max(0.0, min(v, 1.0)) if math.isfinite(v) else DEFAULT_MAX_CRYPTO_PCT


def public(store: ConfigStore) -> dict[str, Any]:
    """What the UI may see: never a key or secret, only a hint."""
    cfg = get_config(store)
    ex = EXCHANGES.get(cfg.get("exchange") or "")
    key = str(cfg.get("api_key") or "")
    verified = cfg.get("verified") or {}
    fees = cfg.get("fees") or (ex["fees"] if ex else None)
    return {
        "exchange": cfg.get("exchange") or "",
        "label": (f"{ex['label']} (sandbox)" if cfg.get("sandbox") else ex["label"]) if ex else "",
        "product": ex["product"] if ex else "",
        "quote": ex["quote"] if ex else "",
        "enabled": bool(cfg.get("enabled", True)),
        "configured": bool(key and cfg.get("api_secret")) or bool(cfg.get("key_ok")),
        "connected": is_connected(store),
        "key_hint": (f"…{key[-4:]}" if len(key) > 8 else "set") if key else "",
        "verified": {k: verified.get(k) for k in ("can_view", "can_trade", "can_withdraw", "method", "at")} if verified else None,
        "needs_verification": bool(key and not _verified_ok(cfg)),
        "fees": {"maker": fees.get("maker"), "taker": fees.get("taker"), "source": fees.get("source") or "published entry tier"} if fees else None,
        "sandbox": bool(cfg.get("sandbox")),
        "max_crypto_pct_of_book": max_crypto_pct(store.get_risk()),
    }


def account_label(store: ConfigStore | None = None) -> str:
    try:
        return public(store).get("label") or "Crypto" if store else "Crypto"
    except Exception:  # noqa: BLE001
        return "Crypto"


# ── Snapshots (portfolio refresh hook) ────────────────────────────────────

_last_cashflow_sync = 0.0
_CASHFLOW_INTERVAL = 6 * 3600


def snapshot_rows(db: Session, store: ConfigStore, fetched_at: datetime) -> tuple[list[AccountSnapshot], list[PositionSnapshot], str | None]:
    """Crypto account + position rows for one portfolio refresh (best-effort:
    an exchange outage never breaks the T212 refresh)."""
    client = client_for(store)
    if client is None:
        return [], [], None
    try:
        balances = client.fetch_balances()
        holdings = client.holdings()
        basis = client.cost_basis()
    except CryptoError as exc:
        logger.warning("crypto snapshot failed: %s", exc)
        return [], [], f"error:crypto:{exc.__class__.__name__}"
    ccy = quote_ccy(client.quote)
    cash = float(balances.get(client.quote, 0.0))
    positions: list[PositionSnapshot] = []
    invested = ppl = 0.0
    for h in holdings:
        value = h["quantity"] * h["price"]
        avg = float(basis.get(h["asset"]) or 0.0) or h["price"]
        cost = avg * h["quantity"]
        invested += value
        ppl += value - cost
        positions.append(PositionSnapshot(
            fetched_at=fetched_at, account_kind=CRYPTO_ACCOUNT, ticker=h["asset"],
            instrument_code=market_symbol(h["asset"], client.quote), quantity=h["quantity"], average_price=avg,
            current_price=h["price"], total_cost=cost, value=value, ppl=value - cost, currency=ccy,
        ))
    account = AccountSnapshot(fetched_at=fetched_at, account_kind=CRYPTO_ACCOUNT, currency=ccy, free_cash=cash,
                              invested=invested, pie_cash=0.0, total=invested + cash, ppl=ppl)
    global _last_cashflow_sync
    if time.time() - _last_cashflow_sync > _CASHFLOW_INTERVAL:
        _last_cashflow_sync = time.time()
        try:
            sync_cashflows(db, client)
        except Exception:  # noqa: BLE001 — contributions are best-effort
            logger.info("crypto cashflow sync failed", exc_info=True)
    return [account], positions, f"crypto-{'sandbox' if client.sandbox else client.exchange_id}"


def sync_cashflows(db: Session, client: CryptoBroker) -> int:
    """Fiat deposits/withdrawals on the exchange → CashflowEvent rows, so the
    return curve nets out money moved onto the exchange (it isn't a gain)."""
    existing = set(db.execute(select(CashflowEvent.reference).where(CashflowEvent.account_kind == CRYPTO_ACCOUNT)).scalars())
    added = 0
    for d in client.fiat_deposits():
        ref = f"crypto-{d.get('id')}"[:64]
        if ref in existing or not d.get("timestamp"):
            continue
        try:
            when = datetime.fromisoformat(str(d["timestamp"]).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        amount = abs(float(d.get("amount") or 0.0)) * (-1 if d.get("type") == "WITHDRAW" else 1)
        db.add(CashflowEvent(account_kind=CRYPTO_ACCOUNT, reference=ref, type=d.get("type") or "DEPOSIT", amount=amount,
                             currency=quote_ccy(str(d.get("currency") or client.quote)), occurred_at=when))
        existing.add(ref)
        added += 1
    if added:
        db.commit()
    return added


def position_meta(ticker: str, instrument_code: str | None, currency: str | None) -> dict[str, Any]:
    """Display/market-data identity for a crypto holding (used by the snapshot)."""
    asset = asset_from_symbol(ticker)
    quote = (instrument_code or "").split("/")[-1] if "/" in (instrument_code or "") else (currency or "GBP")
    return {"name": CRYPTO_ASSETS.get(asset) or asset, "display_ticker": asset,
            "instrument_currency": quote_ccy(quote), "yfinance_ticker": yfinance_symbol(asset, quote)}


# ── Book figures from the latest snapshot ─────────────────────────────────

def _latest_ts(db: Session) -> datetime | None:
    return db.execute(select(AccountSnapshot.fetched_at).order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()


def book_figures(db: Session) -> dict[str, float] | None:
    """``{book_gbp, crypto_gbp}`` from the latest snapshots (all accounts, GBP),
    plus crypto bought/sold since the crypto snapshot. None if no snapshot.

    Uses the latest *crypto* snapshot even if the most recent refresh couldn't
    reach the exchange — a failed refresh must never read as "no crypto"."""
    from app.services.fx import get_fx_rate

    ts = _latest_ts(db)
    if ts is None:
        return None
    accounts = db.execute(select(AccountSnapshot).where(AccountSnapshot.fetched_at == ts)).scalars().all()
    crypto_acct = db.execute(select(AccountSnapshot).where(AccountSnapshot.account_kind == CRYPTO_ACCOUNT)
                             .order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalars().first()
    book = sum(max(float(a.total or 0.0), 0.0) * get_fx_rate(a.currency or "GBP", "GBP")
               for a in accounts if a.account_kind != CRYPTO_ACCOUNT)
    crypto = 0.0
    crypto_ts = ts
    if crypto_acct is not None:
        rate = get_fx_rate(crypto_acct.currency or "GBP", "GBP")
        crypto = max(float(crypto_acct.invested or 0.0), 0.0) * rate
        book += max(float(crypto_acct.total or 0.0), 0.0) * rate
        crypto_ts = crypto_acct.fetched_at
    # Executed since the crypto snapshot: a second tranche approved before the
    # next refresh must still count the first one.
    since = db.execute(select(TradeIntent).where(TradeIntent.status == "executed",
                                                 TradeIntent.executed_at >= crypto_ts)).scalars().all()
    delta = 0.0
    for i in since:
        if (i.meta or {}).get("account_kind") == CRYPTO_ACCOUNT:
            delta += float(i.estimated_notional or 0.0) * (1 if i.side == "buy" else -1)
    return {"book_gbp": book, "crypto_gbp": max(crypto + delta, 0.0)}


def crypto_rail_violations(db: Session, *, side: str, estimated_notional: float, risk: dict[str, Any]) -> list[str]:
    """Crypto-only rails, on top of the shared ones. Checked at proposal time and
    again by ``execute_intent`` (via ``rail_violations``)."""
    out: list[str] = []
    store = ConfigStore(db)
    if not is_connected(store):
        out.append("crypto-guard: no verified trade-only crypto exchange key is connected")
    if side != "buy":
        return out
    cap = max_crypto_pct(risk)
    figs = book_figures(db)
    if not figs or figs["book_gbp"] <= 0:
        out.append("crypto-guard: no portfolio snapshot to size crypto against")
        return out
    after = figs["crypto_gbp"] + float(estimated_notional or 0.0)
    pct = after / figs["book_gbp"]
    if pct > cap + 1e-9:
        room = max(cap * figs["book_gbp"] - figs["crypto_gbp"], 0.0)
        out.append(f"crypto-guard: crypto would be {pct:.1%} of the book (cap {cap:.0%}); room left £{room:,.2f}")
    return out


# ── Pricing + cost estimates ──────────────────────────────────────────────

def _yf_price_gbp(asset: str) -> float | None:
    from app.services.market_data import fetch_history

    try:
        px = float(fetch_history(yfinance_symbol(asset, "GBP"), lookback_days=10)["close"].iloc[-1])
        return px if math.isfinite(px) and px > 0 else None
    except Exception:  # noqa: BLE001
        return None


def quote_estimate(client: CryptoBroker | None, exchange_id: str, asset: str, side: str, notional_gbp: float | None = None,
                   *, fees: dict[str, Any] | None = None) -> dict[str, Any]:
    """Price in the exchange quote currency + fee and slippage estimates.

    Slippage for a small market order on a liquid pair ≈ half the bid/ask
    spread, floored at the exchange's typical minimum; it is an estimate."""
    from app.services.fx import get_fx_rate

    spec = EXCHANGES[exchange_id]
    quote = spec["quote"]
    to_gbp = get_fx_rate(quote_ccy(quote), "GBP")
    bid = ask = last = None
    if client is not None:
        try:
            t = client.fetch_ticker(asset)
            bid, ask, last = t.get("bid") or None, t.get("ask") or None, t.get("last") or None
        except CryptoError as exc:
            logger.info("crypto ticker failed: %s", exc)
    if not last:
        gbp = _yf_price_gbp(asset)
        if gbp is None:
            raise CryptoError(f"no price for {asset}")
        last = gbp / to_gbp
    price = (ask if side == "buy" else bid) or last
    half_spread = ((ask - bid) / 2 / last) if (bid and ask and last and ask > bid) else 0.0
    slippage = max(spec["min_slippage"], half_spread)
    if fees is None and client is not None:
        try:
            fees = client.trading_fees(asset)
        except CryptoError:
            fees = None
    fees = fees or {**spec["fees"], "source": "published entry tier"}
    taker = float(fees.get("taker") or spec["fees"]["taker"])
    out = {"exchange": exchange_id, "label": spec["label"], "quote": quote, "market": market_symbol(asset, quote),
           "price": float(price), "price_gbp": float(price) * to_gbp, "bid": bid, "ask": ask,
           "fee_rate": taker, "fee_source": fees.get("source") or "published entry tier", "slippage_pct": round(slippage, 6)}
    if notional_gbp:
        out["fee_gbp"] = round(notional_gbp * taker, 2)
        out["slippage_gbp"] = round(notional_gbp * slippage, 2)
        out["all_in_cost_gbp"] = round(out["fee_gbp"] + out["slippage_gbp"], 2)
        out["all_in_cost_pct"] = round(taker + slippage, 6)
    return out


# ── Proposals (called from proposal_service.propose_trade) ────────────────

def propose_crypto_trade(
    db: Session, *, side: str, rationale: str, symbol: str | None, instrument_code: str | None,
    quantity: float | None, notional_gbp: float | None, order_type: str, play_id: str | None, source: str,
    caps: dict[str, Any], risk: dict[str, Any],
) -> dict[str, Any]:
    from app.services.execution_service import rail_violations
    from app.services.proposal_service import _SOFT_VIOLATIONS, serialize_intent

    def _fail(message: str) -> dict[str, Any]:
        return {"ok": False, "error": message, "caps": caps}

    store = ConfigStore(db)
    cap = max_crypto_pct(risk)
    caps = {**caps, "max_crypto_pct_of_book": cap}
    asset = asset_from_symbol(instrument_code or symbol)
    if asset not in CRYPTO_ASSETS:
        return _fail(f"{asset or symbol!r} isn't a supported crypto asset — use one of {', '.join(CRYPTO_ASSETS)}")
    if order_type != "market":
        return _fail("crypto proposals are market orders (sized in GBP); drop limit/stop prices")
    if not is_connected(store):
        return _fail("no crypto exchange is connected — the user connects one (trade-only key) in Settings → Crypto")
    cfg = get_config(store)
    client = client_for(store)
    try:
        est = quote_estimate(client, cfg["exchange"], asset, side, fees=cfg.get("fees"))
    except CryptoError as exc:
        return _fail(f"could not price {asset}: {exc}")
    price_gbp = est["price_gbp"]
    if not price_gbp or price_gbp <= 0:
        return _fail(f"could not price {asset}")
    qty = quantity if quantity is not None else math.floor((float(notional_gbp or 0) / price_gbp) * 1e8) / 1e8
    if not qty or qty <= 0:
        return _fail("notional_gbp is too small")
    estimated_notional = round(qty * price_gbp, 2)
    est = {**est, **{k: v for k, v in quote_estimate(None, cfg["exchange"], asset, side, estimated_notional,
                                                       fees={"taker": est["fee_rate"], "source": est["fee_source"]}).items()
                     if k in {"fee_gbp", "slippage_gbp", "all_in_cost_gbp", "all_in_cost_pct"}}}
    market = est["market"]
    violations = rail_violations(db, symbol=asset, side=side, quantity=qty, estimated_notional=estimated_notional,
                                 risk=risk, account_kind=CRYPTO_ACCOUNT, instrument_code=market)
    hard = [v for v in violations if v not in _SOFT_VIOLATIONS]
    warnings = [v for v in violations if v in _SOFT_VIOLATIONS]
    sizing = {"symbol": asset, "instrument_code": market, "quantity": qty, "reference_price": est["price"],
              "currency": quote_ccy(est["quote"]), "estimated_notional_gbp": estimated_notional,
              "fee_gbp": est.get("fee_gbp"), "slippage_gbp": est.get("slippage_gbp")}
    if hard:
        return {"ok": False, "violations": hard, "sizing": sizing, "caps": caps}

    meta: dict[str, Any] = {
        "source": (source or "agent")[:64], "account_kind": CRYPTO_ACCOUNT, "reference_price": est["price"],
        "currency": quote_ccy(est["quote"]), "price_gbp": round(price_gbp, 6),
        "instrument_name": CRYPTO_ASSETS[asset], "crypto": est,
    }
    if notional_gbp is not None:
        meta["requested_notional_gbp"] = notional_gbp
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
    intent = TradeIntent(
        status="proposed", broker_mode=str(store.get_broker().get("broker_mode", "paper")), symbol=asset,
        instrument_code=market, side=side, order_type="market", quantity=qty, estimated_notional=estimated_notional,
        rationale=(rationale or "")[:8000], meta=meta,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return {
        "ok": True, "intent": serialize_intent(intent), "warnings": warnings, "caps": caps,
        "costs": {"fee_gbp": est.get("fee_gbp"), "slippage_gbp": est.get("slippage_gbp"), "fee_rate": est["fee_rate"],
                  "exchange": est["label"], "market": market},
        "note": "Proposed only — nothing was sent to the exchange. A human must approve it in the app.",
    }


def wrong_account_hint(symbol: str | None, instrument_code: str | None) -> str | None:
    """A crypto symbol proposed into a Trading 212 account (without an explicit
    T212 instrument code) is almost certainly a routing mistake."""
    if instrument_code or not is_crypto_symbol(symbol):
        return None
    asset = asset_from_symbol(symbol)
    return (f"{asset} is a crypto asset: propose it with account_kind='crypto' (it goes to the connected exchange). "
            "For a crypto ETN/ETP on Trading 212 instead, pass its instrument_code.")


# ── Risk: volatility + correlation with the rest of the book ──────────────

_risk_cache: tuple[float, dict[str, Any]] | None = None
_RISK_TTL = 30 * 60


def _returns(ticker: str, days: int = 400):
    from app.services.market_data import fetch_history

    f = fetch_history(ticker, lookback_days=days)
    s = f.set_index("date")["close"].astype(float)
    s.index = [str(d)[:10] for d in s.index]
    return s.pct_change().dropna()


def crypto_risk(db: Session, *, force: bool = False) -> dict[str, Any]:
    """Crypto sleeve risk: per-asset annualised vol (365-day year), 1y max
    drawdown, correlation of each asset with the rest of the book, and what a
    50% crypto fall would cost. BTC is shown even with no holdings yet."""
    global _risk_cache
    if not force and _risk_cache and time.time() - _risk_cache[0] < _RISK_TTL:
        return _risk_cache[1]
    import numpy as np
    import pandas as pd

    from app.services.portfolio_service import get_portfolio_snapshot

    snap = get_portfolio_snapshot(db, account_kind="all", display_currency="GBP")
    total = float(snap["metrics"]["total_value"] or 0.0)
    positions = snap.get("positions") or []
    crypto_pos = [p for p in positions if p.get("account_kind") == CRYPTO_ACCOUNT]
    others = sorted([p for p in positions if p.get("account_kind") != CRYPTO_ACCOUNT and p.get("yfinance_ticker")],
                    key=lambda p: -(p.get("value") or 0))[:10]
    crypto_value = sum(float(p.get("value") or 0) for p in crypto_pos)

    # The rest of the book as one value-weighted daily return series.
    book_ret = None
    try:
        frames, weights = [], []
        for p in others:
            try:
                frames.append(_returns(p["yfinance_ticker"]).rename(p["yfinance_ticker"]))
                weights.append(float(p.get("value") or 0))
            except Exception:  # noqa: BLE001
                continue
        if frames:
            df = pd.concat(frames, axis=1).dropna(how="all").fillna(0.0)
            w = np.array(weights) / (sum(weights) or 1.0)
            book_ret = (df * w).sum(axis=1)
    except Exception:  # noqa: BLE001
        book_ret = None

    assets = sorted({p["ticker"] for p in crypto_pos} or {"BTC"})
    rows = []
    for asset in assets:
        yf = yfinance_symbol(asset, "GBP")
        row: dict[str, Any] = {"asset": asset, "name": CRYPTO_ASSETS.get(asset, asset), "yfinance_ticker": yf}
        try:
            r = _returns(yf, 400)
            row["volatility"] = float(r.tail(365).std(ddof=0) * math.sqrt(365))
            closes = (1 + r.tail(365)).cumprod()
            row["max_drawdown_1y"] = float((closes / closes.cummax() - 1).min())
            if book_ret is not None:
                joined = pd.concat([r.rename("c"), book_ret.rename("b")], axis=1, join="inner").dropna().tail(250)
                row["correlation_to_book"] = float(joined["c"].corr(joined["b"])) if len(joined) > 40 else None
        except Exception:  # noqa: BLE001
            row.setdefault("volatility", None)
        held = next((p for p in crypto_pos if p["ticker"] == asset), None)
        row["value_gbp"] = float(held.get("value") or 0) if held else 0.0
        rows.append(row)

    book_vol = None
    if book_ret is not None and len(book_ret) > 40:
        book_vol = float(book_ret.tail(250).std(ddof=0) * math.sqrt(252))
    sleeve_vol = None
    if crypto_value > 0:
        sleeve_vol = sum((r.get("volatility") or 0) * r["value_gbp"] for r in rows) / crypto_value
    cap = max_crypto_pct(ConfigStore(db).get_risk())
    out = {
        "as_of": datetime.utcnow().isoformat(),
        "crypto_value_gbp": round(crypto_value, 2),
        "book_gbp": round(total, 2),
        "crypto_weight": (crypto_value / total) if total else 0.0,
        "cap_pct": cap,
        "assets": rows,
        "sleeve_volatility": sleeve_vol,
        "book_volatility_ex_crypto": book_vol,
        "stress_50pct_gbp": round(crypto_value * 0.5, 2),
        "stress_50pct_of_book": (crypto_value * 0.5 / total) if total else 0.0,
    }
    _risk_cache = (time.time(), out)
    return out


def overview(db: Session) -> dict[str, Any]:
    """Connection + latest crypto balances/positions (from the stored snapshot)."""
    store = ConfigStore(db)
    pub = public(store)
    ts = db.execute(select(AccountSnapshot.fetched_at).where(AccountSnapshot.account_kind == CRYPTO_ACCOUNT)
                    .order_by(desc(AccountSnapshot.fetched_at)).limit(1)).scalar_one_or_none()
    account = positions = None
    if ts is not None:
        a = db.execute(select(AccountSnapshot).where(AccountSnapshot.account_kind == CRYPTO_ACCOUNT,
                                                     AccountSnapshot.fetched_at == ts)).scalars().first()
        account = {"fetched_at": ts.isoformat(), "currency": a.currency, "free_cash": a.free_cash,
                   "invested": a.invested, "total": a.total, "ppl": a.ppl} if a else None
        positions = [{"asset": p.ticker, "market": p.instrument_code, "quantity": p.quantity, "price": p.current_price,
                      "average_price": p.average_price, "value": p.value, "ppl": p.ppl, "currency": p.currency}
                     for p in db.execute(select(PositionSnapshot).where(PositionSnapshot.account_kind == CRYPTO_ACCOUNT,
                                                                        PositionSnapshot.fetched_at == ts)).scalars()]
    return {"connection": pub, "account": account, "positions": positions or []}
