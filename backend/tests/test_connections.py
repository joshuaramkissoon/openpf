"""Connections: many brokers/exchanges at once, read-only first.

* read-only accounts (IBKR, SnapTrade, a query-only crypto key) can be analysed
  and get suggestions, but ``execute_intent`` refuses them — also when re-routed
  to another account, and under a standing permission;
* several crypto exchanges at once, each read-only or trade (never withdraw);
* secrets encrypted at rest, never returned, never decrypted in an agent process;
* sandbox: only offline fixtures, real provider clients refuse to exist;
* agent hooks/env block the new provider hosts and keys.

In-memory DB, fakes only — no network."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import AccountSnapshot, AppConfig, PositionSnapshot, TradeIntent
from app.services import (
    broker_router,
    connections_service,
    crypto_service,
    execution_service,
    proposal_service,
    provider_clients,
)
from app.services.config_store import ConfigStore
from app.services.crypto_client import CryptoBroker
from app.services.provider_clients import ProviderAccount, ProviderPosition


@pytest.fixture(autouse=True)
def _secrets_key(monkeypatch):
    monkeypatch.setenv("OPENPF_SECRETS_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("OPENPF_SANDBOX", raising=False)
    monkeypatch.delenv("OPENPF_MCP_CONTEXT", raising=False)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeExchange(CryptoBroker):
    def __init__(self, exchange_id="kraken", quote="GBP", perms=None, balances=None):
        self.exchange_id = exchange_id
        self.quote = quote
        self.perms = perms or {"can_view": True, "can_trade": True, "can_withdraw": False, "method": "fake"}
        self.balances = balances if balances is not None else {quote: 400.0, "BTC": 0.002}
        self.orders: list[tuple] = []

    def fetch_balances(self):
        return dict(self.balances)

    def fetch_ticker(self, asset):
        px = {"BTC": 60000.0, "ETH": 2000.0, "SOL": 140.0}.get(asset, 100.0)
        return {"bid": px * 0.999, "ask": px * 1.001, "last": px}

    def place_market_order(self, asset, side, amount, *, reference_price=None):
        self.orders.append((asset, side, amount))
        return {"id": f"{self.exchange_id}-1", "status": "closed", "average": 60000.0, "filled": amount, "fee": {}}

    def fetch_order(self, order_id, asset):
        return None

    def trading_fees(self, asset="BTC"):
        return {"maker": 0.001, "taker": 0.002, "source": "fake"}

    def key_permissions(self):
        return dict(self.perms)


@pytest.fixture()
def exchanges(monkeypatch):
    """One fake per exchange id, keyed by the API key's prefix for permissions."""
    made: dict[str, FakeExchange] = {}

    def build(exchange_id, key, secret):
        perms = {"can_view": True, "can_trade": not key.startswith("READ"), "can_withdraw": key.startswith("WD"),
                 "method": "fake"}
        ex = made.get(exchange_id) or FakeExchange(exchange_id, "USDC" if exchange_id == "binance" else "GBP")
        ex.perms = perms
        made[exchange_id] = ex
        return ex

    monkeypatch.setattr(crypto_service, "build_crypto_client", build)
    return made


IBKR_ACCOUNT = ProviderAccount(
    external_id="U1234567", name="Individual", currency="GBP", cash=500.0, institution="Interactive Brokers",
    positions=[ProviderPosition(symbol="MSFT", name="Microsoft", quantity=4, price=400.0, currency="USD", avg_cost=300.0,
                                yfinance="MSFT"),
               ProviderPosition(symbol="VWRL", name="Vanguard All-World", quantity=10, price=100.0, currency="GBP",
                                avg_cost=90.0, yfinance="VWRL.L")],
)


class FakeProvider(provider_clients.ProviderClient):
    provider = "ibkr"
    fail: Exception | None = None

    def fetch(self):
        if FakeProvider.fail:
            raise FakeProvider.fail
        return [IBKR_ACCOUNT]


@pytest.fixture()
def ibkr(monkeypatch):
    FakeProvider.fail = None
    monkeypatch.setattr(provider_clients, "build_provider_client", lambda p, s, settings=None: FakeProvider())
    monkeypatch.setattr("app.services.fx.get_fx_rate", lambda a, b: 0.8 if (a, b) == ("USD", "GBP") else
                        (1.25 if (a, b) == ("GBP", "USD") else 1.0))
    return FakeProvider


def _connect_ibkr(db):
    return connections_service.add_connection(db, "ibkr", {"token": "IBKR-TOKEN-SECRET-123", "query_id": "912345"})


def _book(db):
    now = datetime.utcnow()
    db.add(AccountSnapshot(fetched_at=now, account_kind="invest", currency="GBP", free_cash=5000, invested=5000, total=10000))
    db.commit()


def _refresh(db, when=None):
    when = when or datetime.utcnow()
    accounts, positions, sources = connections_service.snapshot_rows(db, ConfigStore(db), when)
    db.add_all(accounts + positions)
    db.commit()
    return accounts, positions, sources


# ── read-only accounts never execute ─────────────────────────────────────

def test_ibkr_holdings_join_the_book_with_names_and_fx(db, ibkr):
    _connect_ibkr(db)
    _book(db)
    accounts, positions, _ = _refresh(db)
    (acct,) = accounts
    assert acct.account_kind == "ibkr:u1234567" and acct.currency == "GBP"
    msft = next(p for p in positions if p.ticker == "MSFT")
    assert msft.value == pytest.approx(4 * 400 * 0.8)  # USD position → GBP account money
    assert acct.total == pytest.approx(500 + 1280 + 1000)
    directory = connections_service.account_directory(ConfigStore(db))
    assert directory["ibkr:u1234567"]["read_only"] and directory["ibkr:u1234567"]["short"] == "IBKR"
    meta = connections_service.position_meta(ConfigStore(db), "ibkr:u1234567", "VWRL", "VWRL.L", "GBP")
    assert meta["name"] == "Vanguard All-World" and meta["yfinance_ticker"] == "VWRL.L"


def test_read_only_proposal_is_a_suggestion_the_gate_refuses(db, ibkr):
    _connect_ibkr(db)
    _book(db)
    _refresh(db)
    out = proposal_service.propose_trade(db, side="sell", account_kind="ibkr:u1234567", symbol="MSFT", quantity=2,
                                         rationale="trim")
    assert out["ok"] and out["read_only"] and out["place_in"] == "Interactive Brokers"
    intent = db.get(TradeIntent, out["intent"]["id"])
    assert intent.meta["read_only"] and intent.status == "proposed" and out["intent"]["place_in"] == "Interactive Brokers"

    for override in (None, "invest", "stocks_isa", "crypto"):
        with pytest.raises(execution_service.ExecutionError, match="Place it yourself in Interactive Brokers"):
            execution_service.execute_intent(db, intent.id, approve=True, account_kind=override)
    db.refresh(intent)
    assert intent.status == "proposed"  # never approved on the way to the refusal

    # Selling more than the IBKR position is refused at proposal time.
    too_many = proposal_service.propose_trade(db, side="sell", account_kind="ibkr:u1234567", symbol="MSFT", quantity=9,
                                              rationale="x")
    assert not too_many["ok"] and "only 4 held" in too_many["violations"][0]


def test_read_only_account_refused_even_on_a_hand_made_intent(db, ibkr):
    _connect_ibkr(db)
    intent = TradeIntent(status="approved", symbol="MSFT", instrument_code="MSFT", side="buy", order_type="market",
                         quantity=1.0, estimated_notional=300.0, meta={"account_kind": "ibkr:u1234567"})
    db.add(intent)
    db.commit()
    with pytest.raises(execution_service.ExecutionError, match="read-only"):
        execution_service.execute_intent(db, intent.id)
    assert broker_router.refusal(db, "ibkr:u1234567").startswith("read-only")
    assert broker_router.refusal(db, "snap:nope").startswith("invalid account")
    assert broker_router.refusal(db, "invest") is None


def test_standing_permission_never_applies_to_read_only(db, ibkr):
    from app.services import permissions_service

    _connect_ibkr(db)
    _book(db)
    _refresh(db)
    out = proposal_service.propose_trade(db, side="buy", account_kind="ibkr:u1234567", symbol="MSFT", quantity=1,
                                         rationale="x")
    verdict = permissions_service.evaluate(db, db.get(TradeIntent, out["intent"]["id"]))
    assert "read-only" in (verdict.get("blocked") or "")


def test_failed_or_throttled_provider_carries_last_rows_forward(db, ibkr):
    _connect_ibkr(db)
    _refresh(db)
    # Inside IBKR's sync interval: not fetched, still in the book.
    ibkr.fail = RuntimeError("should not be called")
    accounts, positions, _ = _refresh(db, datetime.utcnow() + timedelta(minutes=1))
    assert [a.account_kind for a in accounts] == ["ibkr:u1234567"] and len(positions) == 2
    # Due again but failing: carried forward, error recorded on the connection.
    connections_service.request_sync(db)
    ibkr.fail = provider_clients.ProviderAuthError("token expired")
    accounts, positions, _ = _refresh(db, datetime.utcnow() + timedelta(minutes=2))
    assert accounts and len(positions) == 2
    (conn,) = [c for c in connections_service.list_connections(db)["connections"] if c["provider"] == "ibkr"]
    assert conn["status"]["state"] == "error" and "reconnect" in conn["status"]["message"]


# ── several crypto exchanges, read or trade, never withdraw ──────────────

def test_multiple_exchanges_at_once_each_read_or_trade(db, exchanges):
    crypto_service.connect(ConfigStore(db), "kraken", "TRADE-KRAKEN-KEY", "S")  # the original slot, trade
    connections_service.add_connection(db, "binance", {"api_key": "READ-BINANCE-KEY", "api_secret": "S"}, mode="read")
    connections_service.add_connection(db, "coinbase", {"api_key": "TRADE-COINBASE-KEY", "api_secret": "S"}, mode="trade")
    kinds = {c["account_kind"]: crypto_service.tradable(c) for c in crypto_service.crypto_configs(ConfigStore(db))}
    assert kinds == {"crypto": True, "crypto:binance": False, "crypto:coinbase": True}
    accounts, _, _ = _refresh(db)
    assert {a.account_kind for a in accounts} == set(kinds)

    _book(db)
    db.add_all(accounts)
    db.commit()
    # Explicit trade account routes to that exchange.
    ConfigStore(db).set_broker({"broker_mode": "live"})
    out = proposal_service.propose_trade(db, side="buy", account_kind="crypto:coinbase", symbol="BTC", notional_gbp=60,
                                         rationale="x")
    assert out["ok"] and out["intent"]["account_kind"] == "crypto:coinbase", out
    execution_service.execute_intent(db, out["intent"]["id"], approve=True)
    assert exchanges["coinbase"].orders and not exchanges["kraken"].orders
    # Read-only exchange: a suggestion, refused at the gate.
    ro = proposal_service.propose_trade(db, side="sell", account_kind="crypto:binance", symbol="BTC", quantity=0.001,
                                        rationale="x")
    assert ro["ok"] and ro["read_only"] and ro["place_in"] == "Binance"
    with pytest.raises(execution_service.ExecutionError, match="Binance"):
        execution_service.execute_intent(db, ro["intent"]["id"], approve=True)
    assert not exchanges["binance"].orders


def test_plain_crypto_routes_to_the_trade_exchange_when_the_original_is_read_only(db, exchanges):
    crypto_service.connect(ConfigStore(db), "kraken", "READ-KRAKEN-KEY", "S", mode="read")
    assert crypto_service.resolve_account(ConfigStore(db), "crypto") == "crypto"  # nothing tradable: stays put
    connections_service.add_connection(db, "coinbase", {"api_key": "TRADE-CB-KEY", "api_secret": "S"}, mode="trade")
    assert crypto_service.resolve_account(ConfigStore(db), "crypto") == "crypto:coinbase"
    assert broker_router.refusal(db, "crypto") is None  # resolves to the coinbase trade account
    assert broker_router.refusal(db, "crypto:coinbase") is None


def test_withdraw_keys_refused_in_every_mode_and_trade_needs_a_trading_key(db, exchanges):
    for mode in ("read", "trade"):
        with pytest.raises(connections_service.ConnectionError_, match="withdraw"):
            connections_service.add_connection(db, "binance", {"api_key": "WD-KEY", "api_secret": "S"}, mode=mode)
    with pytest.raises(connections_service.ConnectionError_, match="can't place orders"):
        connections_service.add_connection(db, "binance", {"api_key": "READ-KEY", "api_secret": "S"}, mode="trade")
    assert connections_service.registry_items(ConfigStore(db)) == []
    with pytest.raises(connections_service.ConnectionError_, match="can't trade on Interactive Brokers"):
        connections_service.add_connection(db, "ibkr", {"token": "t", "query_id": "1"}, mode="trade")


def test_duplicate_exchange_is_refused(db, exchanges):
    connections_service.add_connection(db, "binance", {"api_key": "READ-1", "api_secret": "S"})
    with pytest.raises(connections_service.ConnectionError_, match="already connected"):
        connections_service.add_connection(db, "binance", {"api_key": "READ-2", "api_secret": "S"})


# ── secrets ──────────────────────────────────────────────────────────────

def test_secrets_are_encrypted_at_rest_and_never_returned(db, exchanges, ibkr):
    _connect_ibkr(db)
    connections_service.add_connection(db, "binance", {"api_key": "READ-BINANCE-SECRETKEY", "api_secret": "BIN-SECRET"})
    raw = json.dumps([r.value for r in db.query(AppConfig).all()])
    for secret in ("IBKR-TOKEN-SECRET-123", "READ-BINANCE-SECRETKEY", "BIN-SECRET"):
        assert secret not in raw
    assert all(i["secret"].startswith("fernet:") for i in connections_service.registry_items(ConfigStore(db)))
    public = json.dumps(connections_service.list_connections(db)) + json.dumps(
        connections_service.account_directory(ConfigStore(db)))
    for secret in ("IBKR-TOKEN-SECRET-123", "READ-BINANCE-SECRETKEY", "BIN-SECRET", "fernet:"):
        assert secret not in public


def test_agent_process_cannot_decrypt_or_build_clients(db, exchanges, ibkr, monkeypatch):
    connections_service.add_connection(db, "binance", {"api_key": "READ-BINANCE-KEY", "api_secret": "S"})
    item = connections_service.registry_items(ConfigStore(db))[0]
    monkeypatch.setenv("OPENPF_MCP_CONTEXT", "chat")
    from app.services.secret_box import open_secret

    assert open_secret(item["secret"]) == {}
    cfg = crypto_service.get_config(ConfigStore(db), "crypto:binance")
    assert cfg["api_key"] == "" and cfg["key_ok"] is True
    assert crypto_service.client_for(ConfigStore(db), "crypto:binance") is None


def test_a_changed_secrets_key_needs_reconnect_not_a_crash(db, ibkr, monkeypatch):
    _connect_ibkr(db)
    monkeypatch.setenv("OPENPF_SECRETS_KEY", Fernet.generate_key().decode())
    connections_service.request_sync(db)
    monkeypatch.setattr(provider_clients, "build_provider_client",
                        lambda p, s, settings=None: (_ for _ in ()).throw(AssertionError("no secret")))
    _refresh(db)  # doesn't raise
    (conn,) = [c for c in connections_service.list_connections(db)["connections"] if c["provider"] == "ibkr"]
    assert conn["status"]["state"] == "error"


# ── sandbox isolation ────────────────────────────────────────────────────

def test_sandbox_builds_only_fixtures_and_real_clients_refuse(monkeypatch):
    from app.core.sandbox import SandboxViolation

    monkeypatch.setenv("OPENPF_SANDBOX", "1")
    for provider, creds in provider_clients.SANDBOX_TOKENS.items():
        client = provider_clients.build_provider_client(provider, creds)
        assert client.sandbox and client.fetch()
        with pytest.raises(provider_clients.ProviderAuthError):
            provider_clients.build_provider_client(provider, {k: "REAL" for k in creds})
    for cls, args in ((provider_clients.IbkrFlexClient, ("t", "q")), (provider_clients.AlpacaClient, ("k", "s")),
                      (provider_clients.SnapTradeClient, ("c", "k"))):
        with pytest.raises(SandboxViolation):
            cls(*args)
    with pytest.raises(SandboxViolation):
        provider_clients.assert_host("https://ndcdyn.interactivebrokers.com/x", provider_clients.IBKR_HOSTS)


def test_sandbox_crypto_connections_are_mock_and_read_only(db, monkeypatch):
    monkeypatch.setenv("OPENPF_SANDBOX", "1")
    monkeypatch.setenv("OPENPF_CRYPTO_OFFLINE", "1")
    with pytest.raises(connections_service.ConnectionError_, match="read-only"):
        connections_service.add_connection(db, "binance", {"api_key": "sandbox-crypto-read", "api_secret": "sandbox"},
                                           mode="trade")
    connections_service.add_connection(db, "binance", {"api_key": "sandbox-crypto-read", "api_secret": "sandbox"})
    with pytest.raises(connections_service.ConnectionError_):
        connections_service.add_connection(db, "coinbase", {"api_key": "REAL-KEY", "api_secret": "x"})
    client = crypto_service.client_for(ConfigStore(db), "crypto:binance")
    assert client.sandbox and client.fetch_balances()
    with pytest.raises(Exception):
        client.place_market_order("SOL", "buy", 1.0)


def test_real_provider_requests_are_pinned_to_their_hosts():
    with pytest.raises(provider_clients.ProviderError, match="unexpected host"):
        provider_clients.assert_host("https://evil.example.com/v2/account", provider_clients.ALPACA_HOSTS)
    provider_clients.assert_host("https://paper-api.alpaca.markets/v2/account", provider_clients.ALPACA_HOSTS)


# ── agent guardrails ─────────────────────────────────────────────────────

@pytest.mark.parametrize("command", [
    "curl https://ndcdyn.interactivebrokers.com/AccountManagement/FlexWebService/SendRequest?t=1",
    "curl -H 'APCA-API-KEY-ID: x' https://api.alpaca.markets/v2/orders",
    "curl https://paper-api.alpaca.markets/v2/orders -d '{}'",
    "curl https://api.snaptrade.com/api/v1/accounts",
    "curl -X POST http://127.0.0.1:8054/api/connections",
    "python -c 'from app.services.secret_box import open_secret'",
    "cat backend/openpf-secrets.key",
])
def test_bash_hook_blocks_provider_hosts_and_secrets(command):
    import asyncio

    from app.services.claude_sdk_config import _bash_guard

    out = asyncio.run(_bash_guard({"tool_input": {"command": command}}, None, None))
    assert out.get("hookSpecificOutput", {}).get("permissionDecision") == "deny", command


def test_provider_env_keys_never_reach_agent_processes(monkeypatch):
    from app.services import mcp_registry

    names = ("IBKR_FLEX_TOKEN", "ALPACA_API_KEY", "APCA_API_SECRET_KEY", "SNAPTRADE_CONSUMER_KEY", "OPENPF_SECRETS_KEY")
    for n in names:
        monkeypatch.setenv(n, "LEAKED")
    env = mcp_registry.sdk_env()
    assert all(env[n] == "" for n in names)


# ── parsers ──────────────────────────────────────────────────────────────

FLEX_XML = """<FlexQueryResponse queryName="OpenPF" type="AF"><FlexStatements count="1">
<FlexStatement accountId="U1234567" fromDate="20261010" toDate="20261010">
<AccountInformation accountId="U1234567" currency="GBP" name="Jane Doe"/>
<OpenPositions>
<OpenPosition accountId="U1234567" currency="USD" assetCategory="STK" symbol="MSFT" description="MICROSOFT CORP"
  listingExchange="NASDAQ" position="4" markPrice="400" costBasisPrice="300" levelOfDetail="SUMMARY" isin="US5949181045"/>
<OpenPosition accountId="U1234567" currency="GBP" assetCategory="STK" symbol="VWRL" description="VANGUARD FTSE ALL-WORLD"
  listingExchange="LSEETF" position="10" markPrice="100" costBasisPrice="90" levelOfDetail="SUMMARY"/>
<OpenPosition accountId="U1234567" currency="GBP" assetCategory="STK" symbol="VWRL" position="10" markPrice="100"
  levelOfDetail="LOT"/>
</OpenPositions>
<CashReport><CashReportCurrency accountId="U1234567" currency="BASE_SUMMARY" endingCash="512.5"/></CashReport>
</FlexStatement></FlexStatements></FlexQueryResponse>"""


def test_flex_statement_parses_positions_and_cash():
    (acct,) = provider_clients.parse_flex_statement(FLEX_XML)
    assert (acct.external_id, acct.currency, acct.cash) == ("U1234567", "GBP", 512.5)
    assert [(p.symbol, p.quantity, p.yfinance) for p in acct.positions] == [("MSFT", 4.0, "MSFT"), ("VWRL", 10.0, "VWRL.L")]
    assert acct.positions[0].name == "Microsoft Corp"


def test_flex_errors_map_to_reconnect_or_wait():
    assert provider_clients._flex_status(
        "<FlexStatementResponse><Status>Fail</Status><ErrorCode>1019</ErrorCode>"
        "<ErrorMessage>Statement generation in progress.</ErrorMessage></FlexStatementResponse>")[1] in provider_clients.IBKR_WAIT_CODES
    assert "1012" in provider_clients.IBKR_AUTH_CODES  # token expired → reconnect


def test_snaptrade_signature_is_hmac_of_sorted_compact_json():
    import base64
    import hashlib
    import hmac

    sig = provider_clients.snaptrade_signature("key", "/api/v1/accounts", "clientId=c&timestamp=1", None)
    expected = base64.b64encode(hmac.new(b"key", b'{"content":null,"path":"/api/v1/accounts","query":"clientId=c&timestamp=1"}',
                                         hashlib.sha256).digest()).decode()
    assert sig == expected


def test_snaptrade_v2_positions_parse_and_skip_derivatives_and_cash_equivalents():
    p = provider_clients.SnapTradeClient._position
    stock = p({"instrument": {"kind": "stock", "symbol": "SHOP.TO", "raw_symbol": "SHOP", "description": "Shopify",
                              "currency": "CAD", "exchange": "XTSE"}, "units": "3", "price": "150.5", "cost_basis": "90",
               "currency": "CAD"})
    assert (stock.symbol, stock.quantity, stock.price, stock.currency, stock.yfinance) == ("SHOP", 3.0, 150.5, "CAD", "SHOP.TO")
    assert p({"instrument": {"kind": "option", "symbol": "AAPL 250117C00150000"}, "units": "1"}) is None
    assert p({"instrument": {"kind": "mutualfund", "symbol": "SPAXX"}, "units": "100", "cash_equivalent": True}) is None
