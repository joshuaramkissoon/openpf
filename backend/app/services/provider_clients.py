"""Read-only clients for brokers and aggregators (non-crypto connections).

Each client turns one connection's credentials into a list of
:class:`ProviderAccount` (cash + positions). They only ever READ: there is no
order method on any of them, and every request is pinned to the provider's own
API hosts. In sandbox mode the real clients refuse to exist; the sandbox
fixtures (``SandboxProvider``) stand in, offline.

* Interactive Brokers — Flex Web Service (a token + Flex Query id). Read-only by
  design: the Flex service can only return reports.
* Alpaca — Trading API ``GET /v2/account`` and ``GET /v2/positions``.
* SnapTrade — an aggregator (Robinhood, Schwab, Fidelity, Vanguard, …) using the
  user's own SnapTrade developer keys and a read-only connection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

logger = logging.getLogger(__name__)


class ProviderError(RuntimeError):
    """The provider call failed (network, 5xx, bad response)."""


class ProviderAuthError(ProviderError):
    """Credentials were rejected or expired — the user must reconnect."""


class ProviderPending(ProviderError):
    """The provider is still preparing data (e.g. a Flex statement); try again later."""


@dataclass
class ProviderPosition:
    symbol: str
    name: str
    quantity: float
    price: float  # in ``currency``
    currency: str
    avg_cost: float = 0.0  # per unit, in ``currency``
    asset_class: str = "equity"  # equity | etf | fund | crypto | bond | option | other
    isin: str | None = None
    yfinance: str | None = None


@dataclass
class ProviderAccount:
    external_id: str
    name: str
    currency: str
    cash: float
    positions: list[ProviderPosition] = field(default_factory=list)
    institution: str | None = None


def _num(v: Any, default: float = 0.0) -> float:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def assert_host(url: str, hosts: set[str]) -> None:
    from app.core.sandbox import SandboxViolation, is_sandbox

    host = (urlparse(url).hostname or "").lower()
    if is_sandbox():
        raise SandboxViolation(f"sandbox mode: refusing provider request to {host!r}")
    if host not in hosts:
        raise ProviderError(f"refusing request to unexpected host {host!r}")


class _Http:
    """A small httpx wrapper: host-pinned, timeouts, no redirects off-host."""

    def __init__(self, hosts: set[str], timeout: float = 20.0):
        from app.core.sandbox import SandboxViolation, is_sandbox

        if is_sandbox():
            raise SandboxViolation("sandbox mode: refusing to build a real provider client")
        self.hosts = {h.lower() for h in hosts}
        self._client = httpx.Client(timeout=timeout, follow_redirects=False)

    def request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        assert_host(url, self.hosts)
        try:
            return self._client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise ProviderError(f"request failed: {exc.__class__.__name__}") from exc


class ProviderClient:
    provider: str = ""
    sandbox: bool = False

    def fetch(self) -> list[ProviderAccount]:  # pragma: no cover — interface
        raise NotImplementedError


# ── Interactive Brokers: Flex Web Service ─────────────────────────────────

IBKR_HOSTS = {"ndcdyn.interactivebrokers.com", "gdcdyn.interactivebrokers.com"}
IBKR_SEND_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest"
IBKR_GET_URL = "https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/GetStatement"
# Flex error codes that mean "the token/query is wrong — reconnect", vs "wait".
IBKR_AUTH_CODES = {"1012", "1013", "1014", "1015", "1016", "1020"}
IBKR_WAIT_CODES = {"1001", "1004", "1005", "1006", "1007", "1008", "1009", "1018", "1019", "1021"}
_IBKR_CLASS = {"STK": "equity", "ETF": "etf", "FUND": "fund", "BOND": "bond", "OPT": "option", "CRYPTO": "crypto",
               "CASH": "other", "FUT": "other", "WAR": "other"}


def ibkr_yfinance(symbol: str, listing_exchange: str | None, currency: str | None) -> str:
    """Best-effort Yahoo ticker for an IBKR listing (LSE → .L, etc.)."""
    sym = (symbol or "").strip().upper().replace(" ", "-")
    exch = (listing_exchange or "").upper()
    suffix = {"LSE": ".L", "LSEETF": ".L", "IBIS": ".DE", "IBIS2": ".DE", "XETRA": ".DE", "FWB": ".F", "SBF": ".PA",
              "AEB": ".AS", "EBS": ".SW", "TSE": ".TO", "ASX": ".AX", "SEHK": ".HK", "BVME": ".MI", "BM": ".MC"}.get(exch, "")
    if not suffix and (currency or "").upper() == "GBP":
        suffix = ".L"
    return f"{sym.rstrip('.')}{suffix}"


def parse_flex_statement(xml_text: str) -> list[ProviderAccount]:
    """FlexQueryResponse XML → accounts. Needs the Open Positions section; Cash
    Report (or Equity Summary) gives cash. Values are converted to the account's
    base currency by the caller via ``fxRateToBase`` where present."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProviderError("IBKR returned a statement that isn't valid XML") from exc
    statements = root.findall(".//FlexStatement")
    if not statements:
        raise ProviderError("the Flex statement has no accounts — check the query's Accounts setting")
    out: list[ProviderAccount] = []
    for st in statements:
        acct_id = st.get("accountId") or ""
        info = st.find("AccountInformation")
        base = ((info.get("currency") if info is not None else None) or "").upper()
        name = (info.get("name") if info is not None else None) or ""
        positions: list[ProviderPosition] = []
        for p in st.findall("./OpenPositions/OpenPosition"):
            level = (p.get("levelOfDetail") or "SUMMARY").upper()
            if level not in {"SUMMARY", ""}:
                continue  # lot-level rows repeat the summary
            qty = _num(p.get("position"))
            if not qty:
                continue
            ccy = (p.get("currency") or base or "USD").upper()
            if not base:
                base = ccy
            sym = (p.get("symbol") or "").strip()
            cat = (p.get("assetCategory") or "STK").upper()
            positions.append(ProviderPosition(
                symbol=sym, name=(p.get("description") or sym).title() if (p.get("description") or "").isupper() else (p.get("description") or sym),
                quantity=qty, price=_num(p.get("markPrice")), currency=ccy, avg_cost=_num(p.get("costBasisPrice")),
                asset_class=_IBKR_CLASS.get(cat, "other"), isin=p.get("isin") or None,
                yfinance=ibkr_yfinance(sym, p.get("listingExchange"), ccy) if cat in {"STK", "ETF", "FUND"} else None,
            ))
        cash = None
        for c in st.findall("./CashReport/CashReportCurrency"):
            if (c.get("currency") or "").upper() == "BASE_SUMMARY":
                cash = _num(c.get("endingCash"))
        if cash is None:
            rows = st.findall("./EquitySummaryInBase/EquitySummaryByReportDateInBase")
            if rows:
                cash = _num(rows[-1].get("cash"))
        out.append(ProviderAccount(external_id=acct_id, name=name or acct_id, currency=base or "USD",
                                   cash=float(cash or 0.0), positions=positions, institution="Interactive Brokers"))
    return out


def _flex_status(xml_text: str) -> tuple[str, str, str, str]:
    """(status, error_code, error_message, reference_code) from a FlexStatementResponse."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ProviderError("IBKR returned an unreadable response") from exc
    return ((root.findtext("Status") or "").strip(), (root.findtext("ErrorCode") or "").strip(),
            (root.findtext("ErrorMessage") or "").strip(), (root.findtext("ReferenceCode") or "").strip())


class IbkrFlexClient(ProviderClient):
    provider = "ibkr"

    def __init__(self, token: str, query_id: str, *, poll_seconds: float = 30.0):
        self.token = (token or "").strip()
        self.query_id = (query_id or "").strip()
        self.poll_seconds = poll_seconds
        self._http = _Http(IBKR_HOSTS, timeout=30.0)

    def _get(self, url: str, params: dict[str, str]) -> str:
        r = self._http.request("GET", url, params=params, headers={"User-Agent": "OpenPF/1.0"})
        if r.status_code >= 500:
            raise ProviderError(f"IBKR Flex service error {r.status_code}")
        return r.text

    def fetch(self) -> list[ProviderAccount]:
        text = self._get(IBKR_SEND_URL, {"t": self.token, "q": self.query_id, "v": "3"})
        status, code, msg, ref = _flex_status(text)
        if status != "Success" or not ref:
            if code in IBKR_AUTH_CODES:
                raise ProviderAuthError(f"IBKR: {msg or 'token or query rejected'} ({code})")
            if code in IBKR_WAIT_CODES:
                raise ProviderPending(f"IBKR: {msg or 'busy, try again shortly'} ({code})")
            raise ProviderError(f"IBKR: {msg or 'request failed'} ({code or status})")
        deadline = time.monotonic() + self.poll_seconds
        delay = 2.0
        while True:
            text = self._get(IBKR_GET_URL, {"t": self.token, "q": ref, "v": "3"})
            if "<FlexQueryResponse" in text:
                return parse_flex_statement(text)
            status, code, msg, _ = _flex_status(text)
            if code in IBKR_AUTH_CODES:
                raise ProviderAuthError(f"IBKR: {msg or 'token rejected'} ({code})")
            if code not in IBKR_WAIT_CODES or time.monotonic() + delay > deadline:
                if code in IBKR_WAIT_CODES:
                    raise ProviderPending(f"IBKR: {msg or 'statement still generating'} ({code})")
                raise ProviderError(f"IBKR: {msg or 'statement failed'} ({code or status})")
            time.sleep(delay)
            delay = min(delay * 1.5, 8.0)


# ── Alpaca: Trading API (read endpoints only) ─────────────────────────────

ALPACA_URLS = {"live": "https://api.alpaca.markets", "paper": "https://paper-api.alpaca.markets"}
ALPACA_HOSTS = {"api.alpaca.markets", "paper-api.alpaca.markets"}


class AlpacaClient(ProviderClient):
    provider = "alpaca"

    def __init__(self, key_id: str, secret: str, environment: str = "live"):
        self.environment = "paper" if environment == "paper" else "live"
        self.base = ALPACA_URLS[self.environment]
        self._headers = {"APCA-API-KEY-ID": (key_id or "").strip(), "APCA-API-SECRET-KEY": (secret or "").strip()}
        self._http = _Http(ALPACA_HOSTS)

    def _get(self, path: str) -> Any:
        r = self._http.request("GET", self.base + path, headers=self._headers)
        if r.status_code in {401, 403}:
            raise ProviderAuthError(f"Alpaca rejected the key ({r.status_code})")
        if r.status_code == 429:
            raise ProviderPending("Alpaca rate limit — try again shortly")
        if r.status_code >= 400:
            raise ProviderError(f"Alpaca error {r.status_code}")
        return r.json()

    def fetch(self) -> list[ProviderAccount]:
        acct = self._get("/v2/account")
        positions = []
        for p in self._get("/v2/positions") or []:
            qty = _num(p.get("qty"))
            if not qty:
                continue
            crypto = p.get("asset_class") == "crypto"
            sym = str(p.get("symbol") or "")
            positions.append(ProviderPosition(
                symbol=sym.replace("/", "") if not crypto else sym.split("/")[0].replace("USD", "") or sym,
                name=sym, quantity=qty, price=_num(p.get("current_price")), currency="USD",
                avg_cost=_num(p.get("avg_entry_price")), asset_class="crypto" if crypto else "equity",
                yfinance=(f"{sym.split('/')[0].replace('USD', '')}-USD" if crypto else sym.replace(".", "-")),
            ))
        number = str(acct.get("account_number") or self.environment)
        return [ProviderAccount(external_id=self.environment, name=f"{'Paper' if self.environment == 'paper' else 'Live'} {number[-4:]}",
                                currency=str(acct.get("currency") or "USD").upper(), cash=_num(acct.get("cash")),
                                positions=positions, institution="Alpaca")]


# ── SnapTrade: aggregator (user's own developer keys) ─────────────────────

SNAPTRADE_BASE = "https://api.snaptrade.com"
SNAPTRADE_HOSTS = {"api.snaptrade.com"}


def snaptrade_signature(consumer_key: str, path: str, query: str, content: Any) -> str:
    """SnapTrade request signature: HMAC-SHA256 (consumer key) over the compact,
    key-sorted JSON of ``{content, path, query}``, base64-encoded."""
    payload = json.dumps({"content": content, "path": path, "query": query}, separators=(",", ":"), sort_keys=True)
    digest = hmac.new(consumer_key.encode(), payload.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class SnapTradeClient(ProviderClient):
    provider = "snaptrade"

    def __init__(self, client_id: str, consumer_key: str, user_id: str = "", user_secret: str = ""):
        self.client_id = (client_id or "").strip()
        self.consumer_key = (consumer_key or "").strip()
        self.user_id = user_id
        self.user_secret = user_secret
        self._http = _Http(SNAPTRADE_HOSTS)

    def _call(self, method: str, path: str, *, params: dict[str, str] | None = None, body: Any = None,
              user: bool = True) -> Any:
        q: dict[str, str] = {"clientId": self.client_id, "timestamp": str(int(time.time()))}
        if user:
            q.update({"userId": self.user_id, "userSecret": self.user_secret})
        q.update(params or {})
        query = urlencode(q)
        full_path = f"/api/v1{path}"
        sig = snaptrade_signature(self.consumer_key, full_path, query, body)
        r = self._http.request(method, f"{SNAPTRADE_BASE}{full_path}?{query}", headers={"Signature": sig},
                               content=json.dumps(body, separators=(",", ":")) if body is not None else None)
        if r.status_code in {401, 403}:
            raise ProviderAuthError(f"SnapTrade rejected the request ({r.status_code}): {r.text[:200]}")
        if r.status_code == 429:
            raise ProviderPending("SnapTrade rate limit — try again shortly")
        if r.status_code >= 400:
            raise ProviderError(f"SnapTrade error {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    def register_user(self, user_id: str) -> str:
        data = self._call("POST", "/snapTrade/registerUser", body={"userId": user_id}, user=False)
        self.user_id = user_id
        self.user_secret = str((data or {}).get("userSecret") or "")
        if not self.user_secret:
            raise ProviderError("SnapTrade didn't return a user secret")
        return self.user_secret

    def login_link(self, *, custom_redirect: str | None = None) -> str:
        body: dict[str, Any] = {"connectionType": "read"}
        if custom_redirect:
            body["customRedirect"] = custom_redirect
        data = self._call("POST", "/snapTrade/login", body=body)
        url = str((data or {}).get("redirectURI") or "")
        if not url.startswith("https://"):
            raise ProviderError("SnapTrade didn't return a connection link")
        return url

    def fetch(self) -> list[ProviderAccount]:
        out: list[ProviderAccount] = []
        for a in self._call("GET", "/accounts") or []:
            acct_id = str(a.get("id") or "")
            if not acct_id:
                continue
            positions = []
            for p in self._call("GET", f"/accounts/{acct_id}/positions") or []:
                sym_outer = p.get("symbol") or {}
                sym = sym_outer.get("symbol") if isinstance(sym_outer.get("symbol"), dict) else sym_outer
                ticker = str((sym or {}).get("symbol") or (sym or {}).get("raw_symbol") or "").strip()
                qty = _num(p.get("units") if p.get("units") is not None else p.get("fractional_units"))
                if not ticker or not qty:
                    continue
                ccy = str(((p.get("currency") or {}).get("code")) or (((sym or {}).get("currency") or {}).get("code")) or "USD").upper()
                kind = str((((sym or {}).get("type") or {}).get("code")) or "cs").lower()
                positions.append(ProviderPosition(
                    symbol=ticker, name=str((sym or {}).get("description") or ticker), quantity=qty, price=_num(p.get("price")),
                    currency=ccy, avg_cost=_num(p.get("average_purchase_price")),
                    asset_class={"cs": "equity", "et": "etf", "oef": "fund", "crypto": "crypto", "bnd": "bond"}.get(kind, "equity"),
                    yfinance=f"{ticker}-USD" if kind == "crypto" else ticker.replace(".", "-"),
                ))
            cash = 0.0
            base = str(((a.get("balance") or {}).get("total") or {}).get("currency") or "USD").upper()
            for b in self._call("GET", f"/accounts/{acct_id}/balances") or []:
                code = str(((b.get("currency") or {}).get("code")) or base).upper()
                amt = _num(b.get("cash"))
                if code == base:
                    cash += amt
                elif amt:
                    from app.services.fx import get_fx_rate

                    cash += amt * get_fx_rate(code, base)
            out.append(ProviderAccount(external_id=acct_id, name=str(a.get("name") or a.get("number") or "Account"),
                                       currency=base, cash=cash, positions=positions,
                                       institution=str(a.get("institution_name") or "") or None))
        return out


# ── Sandbox fixtures (offline, fake keys only) ────────────────────────────

SANDBOX_TOKENS = {
    "ibkr": {"token": "sandbox-ibkr", "query_id": "000000"},
    "alpaca": {"key_id": "sandbox-alpaca", "secret": "sandbox"},
    "snaptrade": {"client_id": "sandbox-snaptrade", "consumer_key": "sandbox"},
}

_FIXTURES: dict[str, list[dict[str, Any]]] = {
    "ibkr": [{
        "external_id": "U7654321", "name": "Individual (sandbox)", "currency": "GBP", "cash": 1240.0,
        "institution": "Interactive Brokers",
        "positions": [
            ("VWRL", "Vanguard FTSE All-World UCITS ETF", 42, 112.40, "GBP", 96.10, "etf", "VWRL.L"),
            ("ASML", "ASML Holding NV", 3, 655.0, "EUR", 590.0, "equity", "ASML.AS"),
            ("MSFT", "Microsoft Corp", 6, 418.0, "USD", 310.0, "equity", "MSFT"),
            ("BRK B", "Berkshire Hathaway Inc Cl B", 4, 462.0, "USD", 401.0, "equity", "BRK-B"),
        ],
    }],
    "alpaca": [{
        "external_id": "paper", "name": "Paper (sandbox)", "currency": "USD", "cash": 5320.0, "institution": "Alpaca",
        "positions": [
            ("SPY", "SPDR S&P 500 ETF Trust", 5, 571.0, "USD", 520.0, "etf", "SPY"),
            ("AMD", "Advanced Micro Devices", 8, 152.0, "USD", 171.0, "equity", "AMD"),
        ],
    }],
    "snaptrade": [{
        "external_id": "rh-sbx-01", "name": "Robinhood Individual (sandbox)", "currency": "USD", "cash": 310.0,
        "institution": "Robinhood",
        "positions": [
            ("AAPL", "Apple Inc", 12, 228.0, "USD", 151.0, "equity", "AAPL"),
            ("HOOD", "Robinhood Markets Inc", 20, 24.5, "USD", 18.0, "equity", "HOOD"),
            ("VOO", "Vanguard S&P 500 ETF", 3, 525.0, "USD", 455.0, "etf", "VOO"),
        ],
    }, {
        "external_id": "schwab-sbx-02", "name": "Schwab Roth IRA (sandbox)", "currency": "USD", "cash": 95.0,
        "institution": "Charles Schwab",
        "positions": [
            ("SCHD", "Schwab US Dividend Equity ETF", 40, 28.1, "USD", 25.6, "etf", "SCHD"),
        ],
    }],
}


class SandboxProvider(ProviderClient):
    """Offline fixture accounts for a sandbox instance. Accepts only the fake
    sandbox credentials (``SANDBOX_TOKENS``)."""

    sandbox = True

    def __init__(self, provider: str, secret: dict[str, Any]):
        self.provider = provider
        expected = SANDBOX_TOKENS.get(provider) or {}
        first = next(iter(expected), None)
        if first is None or str(secret.get(first) or "").strip() != expected[first]:
            raise ProviderAuthError(f"sandbox mode only accepts the sandbox {provider} credentials "
                                    f"({first}={expected.get(first, '?')})")

    def fetch(self) -> list[ProviderAccount]:
        out = []
        for a in _FIXTURES.get(self.provider) or []:
            positions = [ProviderPosition(symbol=s, name=n, quantity=float(q), price=px, currency=c, avg_cost=avg,
                                          asset_class=cls, yfinance=yf)
                         for s, n, q, px, c, avg, cls, yf in a["positions"]]
            out.append(ProviderAccount(external_id=a["external_id"], name=a["name"], currency=a["currency"],
                                       cash=a["cash"], positions=positions, institution=a.get("institution")))
        return out


def build_provider_client(provider: str, secret: dict[str, Any], settings: dict[str, Any] | None = None) -> ProviderClient:
    """The one constructor the app uses. Sandbox → fixtures, always."""
    from app.core.sandbox import is_sandbox

    settings = settings or {}
    if is_sandbox():
        return SandboxProvider(provider, secret)
    if provider == "ibkr":
        return IbkrFlexClient(secret.get("token", ""), str(settings.get("query_id") or secret.get("query_id") or ""))
    if provider == "alpaca":
        return AlpacaClient(secret.get("key_id", ""), secret.get("secret", ""), str(settings.get("environment") or "live"))
    if provider == "snaptrade":
        return SnapTradeClient(secret.get("client_id", ""), secret.get("consumer_key", ""),
                               secret.get("user_id", ""), secret.get("user_secret", ""))
    raise ProviderError(f"unknown provider {provider!r}")
