"""Crypto broker clients — the crypto counterpart of ``t212_client``.

One interface (:class:`CryptoBroker`) with two implementations:

* :class:`CcxtCryptoBroker` — a real exchange (Kraken, Coinbase Advanced Trade,
  Binance) through CCXT. Every HTTP request is checked against the exchange's
  own API hosts, and the class refuses to exist at all in sandbox mode.
* :class:`SandboxCryptoBroker` — the in-process mock exchange
  (``crypto_sandbox``). The only client a sandbox instance can build.

Keys are used by the backend process only (``build_crypto_client``); they are
never written to any agent / MCP subprocess environment.
"""

from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from typing import Any
from urllib.parse import urlparse

from app.services.crypto_exchanges import (
    CASH_ASSETS,
    CRYPTO_ASSETS,
    EXCHANGES,
    SANDBOX_EXCHANGE,
    asset_from_symbol,
    market_symbol,
)

logger = logging.getLogger(__name__)


class CryptoError(RuntimeError):
    """Exchange call failed."""


class CryptoAuthError(CryptoError):
    """The key was rejected (bad key/secret, expired, missing permission)."""


class CryptoNotPlaced(CryptoError):
    """The order definitely did not reach the exchange (safe to retry)."""


class CryptoBroker(ABC):
    exchange_id: str
    quote: str
    sandbox: bool = False

    @property
    def label(self) -> str:
        return EXCHANGES[self.exchange_id]["label"]

    @abstractmethod
    def fetch_balances(self) -> dict[str, float]:
        """Total balance per asset (cash and coins), zero balances dropped."""

    @abstractmethod
    def fetch_ticker(self, asset: str) -> dict[str, float]:
        """``{bid, ask, last}`` for ``asset`` in the quote currency."""

    @abstractmethod
    def place_market_order(self, asset: str, side: str, amount: float, *, reference_price: float | None = None) -> dict[str, Any]:
        """Market order for ``amount`` units of ``asset``. Returns a CCXT-shaped order."""

    @abstractmethod
    def fetch_order(self, order_id: str, asset: str) -> dict[str, Any] | None:
        """Status of an order placed earlier."""

    @abstractmethod
    def trading_fees(self, asset: str = "BTC") -> dict[str, Any]:
        """``{maker, taker, source}`` as fractions (0.004 = 0.40%)."""

    @abstractmethod
    def key_permissions(self) -> dict[str, Any]:
        """``{can_view, can_trade, can_withdraw, method, detail}``. ``can_withdraw``
        is True/False when the exchange told us, None when it could not be read."""

    def cost_basis(self) -> dict[str, float]:
        """Best-effort average cost per held asset in the quote currency."""
        return {}

    def fiat_deposits(self) -> list[dict[str, Any]]:
        """Best-effort deposits/withdrawals of fiat (for netting contributions)."""
        return []

    def holdings(self) -> list[dict[str, Any]]:
        """Non-cash balances with live prices: ``[{asset, quantity, price}]``."""
        out = []
        for asset, qty in self.fetch_balances().items():
            a = asset_from_symbol(asset)
            if a in CASH_ASSETS or a not in CRYPTO_ASSETS or qty <= 0:
                continue
            try:
                px = float(self.fetch_ticker(a).get("last") or 0.0)
            except CryptoError:
                px = 0.0
            out.append({"asset": a, "quantity": float(qty), "price": px})
        return out

    def cash(self) -> float:
        return float(self.fetch_balances().get(self.quote, 0.0))


# ── Sandbox ────────────────────────────────────────────────────────────────

class SandboxCryptoBroker(CryptoBroker):
    sandbox = True

    def __init__(self, api_key: str, api_secret: str):
        from app.services import crypto_sandbox as sbx

        self._sbx = sbx
        self.exchange_id = SANDBOX_EXCHANGE
        self.quote = EXCHANGES[SANDBOX_EXCHANGE]["quote"]
        self.api_key = api_key
        self.api_secret = api_secret

    @property
    def label(self) -> str:
        return f"{EXCHANGES[self.exchange_id]['label']} (sandbox)"

    def _auth(self) -> dict[str, Any]:
        try:
            return self._sbx.check_key(self.api_key, self.api_secret)
        except self._sbx.SandboxExchangeError as exc:
            raise CryptoAuthError(str(exc)) from exc

    def fetch_balances(self) -> dict[str, float]:
        self._auth()
        return self._sbx.balances()

    def fetch_ticker(self, asset: str) -> dict[str, float]:
        return self._sbx.ticker(asset)

    def place_market_order(self, asset: str, side: str, amount: float, *, reference_price: float | None = None) -> dict[str, Any]:
        if not self._auth().get("can_trade"):
            raise CryptoNotPlaced("this key cannot trade")
        try:
            return self._sbx.place_market_order(asset, side, amount)
        except self._sbx.SandboxExchangeError as exc:
            raise CryptoNotPlaced(str(exc)) from exc

    def fetch_order(self, order_id: str, asset: str) -> dict[str, Any] | None:
        return self._sbx.fetch_order(order_id)

    def trading_fees(self, asset: str = "BTC") -> dict[str, Any]:
        fees = EXCHANGES[self.exchange_id]["fees"]
        return {"maker": fees["maker"], "taker": fees["taker"], "source": "sandbox schedule"}

    def key_permissions(self) -> dict[str, Any]:
        perms = self._auth()
        return {**perms, "method": "sandbox key", "detail": "Mock exchange key permissions."}

    def cost_basis(self) -> dict[str, float]:
        return self._sbx.cost_basis()

    def fiat_deposits(self) -> list[dict[str, Any]]:
        return [{**d, "type": "DEPOSIT"} for d in self._sbx.deposits()]


class SandboxFixtureCryptoBroker(SandboxCryptoBroker):
    """A read-only sandbox exchange with fixed balances (Binance / Coinbase / a
    query-only Kraken key) — never trades, never touches the mock order book."""

    def __init__(self, exchange_id: str, api_key: str, api_secret: str):
        super().__init__(api_key, api_secret)
        self.exchange_id = exchange_id
        self.quote = EXCHANGES[exchange_id]["quote"]
        self._fixture = self._sbx.FIXTURE_BALANCES.get(exchange_id) or {"balances": {}, "cost_mult": {}}

    def _to_quote(self) -> float:
        from app.services.crypto_exchanges import quote_ccy
        from app.services.fx import get_fx_rate

        return get_fx_rate("GBP", quote_ccy(self.quote))

    def fetch_balances(self) -> dict[str, float]:
        self._auth()
        return {k: float(v) for k, v in self._fixture["balances"].items()}

    def fetch_ticker(self, asset: str) -> dict[str, float]:
        rate = self._to_quote()
        return {k: v * rate for k, v in self._sbx.ticker(asset).items()}

    def place_market_order(self, asset: str, side: str, amount: float, *, reference_price: float | None = None) -> dict[str, Any]:
        raise CryptoNotPlaced("this sandbox exchange is read-only")

    def cost_basis(self) -> dict[str, float]:
        rate = self._to_quote()
        return {a: self._sbx.price_gbp(a) * m * rate for a, m in self._fixture["cost_mult"].items()}

    def fiat_deposits(self) -> list[dict[str, Any]]:
        return []


# ── Real exchanges via CCXT ────────────────────────────────────────────────

class CcxtCryptoBroker(CryptoBroker):
    def __init__(self, exchange_id: str, api_key: str, api_secret: str, *, timeout_ms: int = 15000):
        from app.core.sandbox import SandboxViolation, is_sandbox

        if is_sandbox():
            # Defence in depth: build_crypto_client never gets here in sandbox mode.
            raise SandboxViolation("sandbox mode: refusing to build a real crypto exchange client")
        if exchange_id not in EXCHANGES:
            raise CryptoError(f"unsupported exchange {exchange_id!r}")
        import ccxt  # imported lazily: only live instances with a connected exchange need it

        spec = EXCHANGES[exchange_id]
        self.exchange_id = exchange_id
        self.quote = spec["quote"]
        self._ccxt = ccxt
        self._hosts = {h.lower() for h in spec["hosts"]}
        cls = getattr(ccxt, spec["ccxt_id"])
        self._ex = cls({"apiKey": api_key, "secret": api_secret, "enableRateLimit": True, "timeout": timeout_ms})
        original_fetch = self._ex.fetch

        def guarded_fetch(url, method="GET", headers=None, body=None):  # noqa: ANN001
            assert_exchange_host(url, self._hosts)
            return original_fetch(url, method, headers, body)

        self._ex.fetch = guarded_fetch  # every REST call goes through the host check
        self._markets_loaded = False

    def _call(self, fn, *args, **kwargs):  # noqa: ANN001
        ccxt = self._ccxt
        try:
            return fn(*args, **kwargs)
        except (ccxt.AuthenticationError, ccxt.PermissionDenied, ccxt.AccountSuspended) as exc:
            raise CryptoAuthError(f"{self.label}: {exc}") from exc
        except ccxt.BaseError as exc:
            raise CryptoError(f"{self.label}: {exc}") from exc

    def _markets(self) -> None:
        if not self._markets_loaded:
            self._call(self._ex.load_markets)
            self._markets_loaded = True

    def fetch_balances(self) -> dict[str, float]:
        bal = self._call(self._ex.fetch_balance)
        totals = bal.get("total") or {}
        return {asset_from_symbol(k) if k in {"XBT", "XXBT"} else k: float(v) for k, v in totals.items() if v and float(v) > 0}

    def fetch_ticker(self, asset: str) -> dict[str, float]:
        t = self._call(self._ex.fetch_ticker, market_symbol(asset, self.quote))
        return {"bid": float(t.get("bid") or 0.0), "ask": float(t.get("ask") or 0.0), "last": float(t.get("last") or t.get("close") or 0.0)}

    def place_market_order(self, asset: str, side: str, amount: float, *, reference_price: float | None = None) -> dict[str, Any]:
        self._markets()
        symbol = market_symbol(asset, self.quote)
        if symbol not in self._ex.markets:
            raise CryptoNotPlaced(f"{self.label} has no {symbol} market")
        try:
            qty = float(self._ex.amount_to_precision(symbol, amount))
        except Exception as exc:  # noqa: BLE001
            raise CryptoNotPlaced(f"amount {amount} is below {self.label}'s minimum for {symbol}") from exc
        if not math.isfinite(qty) or qty <= 0:
            raise CryptoNotPlaced("order amount rounds to zero")
        params: dict[str, Any] = {}
        if side == "buy" and self.exchange_id == "coinbase":
            # Coinbase market buys are sized in quote currency; CCXT computes
            # cost = amount × price from the reference price.
            if not reference_price:
                raise CryptoNotPlaced("a reference price is needed for a Coinbase market buy")
            return self._call(self._ex.create_order, symbol, "market", side, qty, reference_price, params)
        return self._call(self._ex.create_order, symbol, "market", side, qty, None, params)

    def fetch_order(self, order_id: str, asset: str) -> dict[str, Any] | None:
        return self._call(self._ex.fetch_order, order_id, market_symbol(asset, self.quote))

    def trading_fees(self, asset: str = "BTC") -> dict[str, Any]:
        default = EXCHANGES[self.exchange_id]["fees"]
        try:
            self._markets()
            fee = self._call(self._ex.fetch_trading_fee, market_symbol(asset, self.quote))
            maker, taker = float(fee.get("maker")), float(fee.get("taker"))
            if math.isfinite(taker) and 0 <= taker < 0.05:
                return {"maker": maker, "taker": taker, "source": "your account's tier"}
        except Exception as exc:  # noqa: BLE001 — fall back to the published entry tier
            logger.info("fee lookup failed on %s: %s", self.exchange_id, exc)
        return {"maker": default["maker"], "taker": default["taker"], "source": "published entry tier"}

    def key_permissions(self) -> dict[str, Any]:
        ccxt = self._ccxt
        # Reading balances proves the key works and can view funds.
        try:
            self._call(self._ex.fetch_balance)
            can_view = True
        except CryptoAuthError as exc:
            if "permission" in str(exc).lower():
                can_view = False
            else:
                raise
        if self.exchange_id == "coinbase":
            p = self._call(self._ex.v3PrivateGetBrokerageKeyPermissions)
            return {"can_view": bool(p.get("can_view")) and can_view, "can_trade": bool(p.get("can_trade")),
                    "can_withdraw": bool(p.get("can_transfer")), "method": "Coinbase key_permissions API",
                    "detail": f"can_view={p.get('can_view')} can_trade={p.get('can_trade')} can_transfer={p.get('can_transfer')}"}
        if self.exchange_id == "binance":
            r = self._call(self._ex.sapiGetAccountApiRestrictions)
            withdraw = any(bool(r.get(k)) for k in ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer"))
            return {"can_view": bool(r.get("enableReading")) and can_view, "can_trade": bool(r.get("enableSpotAndMarginTrading")),
                    "can_withdraw": withdraw, "method": "Binance apiRestrictions API",
                    "leverage": bool(r.get("enableMargin") or r.get("enableFutures")),
                    "detail": ", ".join(f"{k}={r.get(k)}" for k in ("enableWithdrawals", "enableInternalTransfer",
                                                                     "permitsUniversalTransfer", "enableSpotAndMarginTrading",
                                                                     "enableMargin", "enableFutures"))}
        if self.exchange_id == "kraken":
            # Kraken has no "what can this key do" endpoint. Probe the withdrawal-INFO
            # endpoint (a read that never moves money) with a made-up address name:
            # "Permission denied" means the key cannot withdraw; any other answer
            # (e.g. "Unknown withdraw key") means the permission check passed.
            can_withdraw: bool | None
            try:
                self._ex.privatePostWithdrawInfo({"asset": "XBT", "key": "openpf-permission-probe", "amount": "0.0001"})
                can_withdraw = True
                detail = "withdraw-info call succeeded"
            except ccxt.PermissionDenied as exc:
                can_withdraw, detail = False, f"withdraw-info: {exc}"
            except ccxt.AuthenticationError as exc:
                raise CryptoAuthError(f"Kraken rejected the key: {exc}") from exc
            except ccxt.ExchangeError as exc:
                text = str(exc)
                can_withdraw = False if "permission denied" in text.lower() else True
                detail = f"withdraw-info: {text}"
            except ccxt.BaseError as exc:  # network etc. — unknown, so the caller refuses
                can_withdraw, detail = None, f"could not probe: {exc}"
            can_trade: bool | None
            try:
                # validate=true checks the order without submitting it.
                self._ex.privatePostAddOrder({"pair": "XBTGBP", "type": "buy", "ordertype": "market",
                                              "volume": "0.0001", "validate": True})
                can_trade = True
            except ccxt.PermissionDenied:
                can_trade = False
            except ccxt.BaseError as exc:
                can_trade = "permission denied" not in str(exc).lower()
            return {"can_view": can_view, "can_trade": can_trade, "can_withdraw": can_withdraw,
                    "method": "Kraken withdraw-info probe", "detail": detail}
        return {"can_view": can_view, "can_trade": None, "can_withdraw": None, "method": "none",
                "detail": "this exchange does not expose key permissions"}

    def cost_basis(self) -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            self._markets()
            for asset in [a for a in self.fetch_balances() if a in CRYPTO_ASSETS]:
                symbol = market_symbol(asset, self.quote)
                if symbol not in self._ex.markets:
                    continue
                qty = cost = 0.0
                for t in sorted(self._call(self._ex.fetch_my_trades, symbol, None, 500), key=lambda x: x.get("timestamp") or 0):
                    amt, px = float(t.get("amount") or 0), float(t.get("price") or 0)
                    if t.get("side") == "buy":
                        cost += amt * px
                        qty += amt
                    elif qty > 0:
                        cost -= (cost / qty) * min(amt, qty)
                        qty = max(qty - amt, 0.0)
                if qty > 0:
                    out[asset] = cost / qty
        except Exception as exc:  # noqa: BLE001 — cost basis is cosmetic
            logger.info("cost basis unavailable on %s: %s", self.exchange_id, exc)
        return out

    def fiat_deposits(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for kind, fn, cap in (("DEPOSIT", "fetch_deposits", "fetchDeposits"), ("WITHDRAW", "fetch_withdrawals", "fetchWithdrawals")):
            if not self._ex.has.get(cap):
                continue
            try:
                for d in self._call(getattr(self._ex, fn), self.quote):
                    if str(d.get("status") or "ok") not in {"ok", "complete", "completed", "success"}:
                        continue
                    out.append({"id": str(d.get("id") or d.get("txid")), "currency": d.get("currency") or self.quote,
                                "amount": float(d.get("amount") or 0.0), "timestamp": d.get("datetime"), "type": kind})
            except Exception as exc:  # noqa: BLE001
                logger.info("%s unavailable on %s: %s", fn, self.exchange_id, exc)
        return out


def assert_exchange_host(url: str, allowed_hosts: set[str]) -> None:
    """Refuse any request that isn't to the exchange's own API host — and, in a
    sandbox instance, refuse every real exchange request outright."""
    from app.core.sandbox import SandboxViolation, is_sandbox

    host = (urlparse(url).hostname or "").lower()
    if is_sandbox():
        raise SandboxViolation(f"sandbox mode: refusing crypto exchange request to {host!r}")
    if host not in allowed_hosts:
        raise CryptoError(f"refusing request to unexpected host {host!r}")


def build_crypto_client(exchange_id: str, api_key: str, api_secret: str) -> CryptoBroker:
    """The one constructor the app uses. Sandbox → the mock exchange, always."""
    from app.core.sandbox import is_sandbox

    if is_sandbox():
        from app.services.crypto_sandbox import SANDBOX_READ_KEY

        if exchange_id != SANDBOX_EXCHANGE or api_key == SANDBOX_READ_KEY:
            return SandboxFixtureCryptoBroker(exchange_id if exchange_id in EXCHANGES else SANDBOX_EXCHANGE, api_key, api_secret)
        return SandboxCryptoBroker(api_key, api_secret)
    return CcxtCryptoBroker(exchange_id, api_key, api_secret)
