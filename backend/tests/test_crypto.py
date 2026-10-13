"""Crypto as part of the book: trade-only key checks, exchange host pinning,
proposals with fee/slippage, the crypto cap, one-book snapshots and the
Starter crypto play. In-memory DB, a fake broker — no network, no real keys."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import AccountSnapshot, Play, PositionSnapshot, TradeIntent
from app.services import crypto_client, crypto_service, execution_service, proposal_service
from app.services.config_store import ConfigStore
from app.services.crypto_client import CryptoBroker, CryptoError


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


class FakeBroker(CryptoBroker):
    """A stand-in exchange: GBP quote, fixed prices, configurable permissions."""

    def __init__(self, perms=None, balances=None):
        self.exchange_id = "kraken"
        self.quote = "GBP"
        self.perms = perms or {"can_view": True, "can_trade": True, "can_withdraw": False, "method": "fake"}
        self.balances = balances if balances is not None else {"GBP": 500.0, "BTC": 0.002}
        self.orders: list[tuple] = []

    def fetch_balances(self):
        return dict(self.balances)

    def fetch_ticker(self, asset):
        px = {"BTC": 60000.0, "ETH": 2000.0}.get(asset, 100.0)
        return {"bid": px * 0.999, "ask": px * 1.001, "last": px}

    def place_market_order(self, asset, side, amount, *, reference_price=None):
        self.orders.append((asset, side, amount))
        return {"id": "K-1", "status": "closed", "average": 60060.0, "filled": amount, "cost": amount * 60060.0,
                "fee": {"cost": 0.5, "currency": "GBP"}}

    def fetch_order(self, order_id, asset):
        return None

    def trading_fees(self, asset="BTC"):
        return {"maker": 0.0025, "taker": 0.004, "source": "fake tier"}

    def key_permissions(self):
        return dict(self.perms)

    def cost_basis(self):
        return {"BTC": 50000.0}


@pytest.fixture()
def broker(monkeypatch):
    b = FakeBroker()
    monkeypatch.setattr(crypto_service, "build_crypto_client", lambda ex, k, s: b)
    return b


def _connect(db, broker):
    return crypto_service.connect(ConfigStore(db), "kraken", "KRAKEN-KEY-1234", "KRAKEN-SECRET")


def _book(db, *, invest_total=10000.0, crypto_cash=500.0, crypto_invested=0.0):
    now = datetime.utcnow()
    db.add(AccountSnapshot(fetched_at=now, account_kind="invest", currency="GBP", free_cash=1000,
                           invested=invest_total - 1000, total=invest_total))
    db.add(AccountSnapshot(fetched_at=now, account_kind="crypto", currency="GBP", free_cash=crypto_cash,
                           invested=crypto_invested, total=crypto_cash + crypto_invested))
    db.commit()


# ── connecting a key: trade-only or nothing ──────────────────────────────

def test_connect_stores_trade_only_key_and_never_returns_it(db, broker):
    pub = _connect(db, broker)
    assert pub["connected"] and pub["exchange"] == "kraken" and pub["quote"] == "GBP"
    assert pub["key_hint"] == "…1234"
    assert "KRAKEN-KEY-1234" not in str(pub) and "KRAKEN-SECRET" not in str(pub)
    assert pub["verified"]["can_withdraw"] is False


@pytest.mark.parametrize("perms, needle", [
    ({"can_view": True, "can_trade": True, "can_withdraw": True}, "can withdraw"),
    ({"can_view": True, "can_trade": True, "can_withdraw": None}, "Couldn't confirm"),
    ({"can_view": False, "can_trade": True, "can_withdraw": False}, "can't read balances"),
    ({"can_view": True, "can_trade": True, "can_withdraw": False, "leverage": True}, "margin or futures"),
])
def test_connect_refuses_unsafe_keys(db, monkeypatch, perms, needle):
    b = FakeBroker(perms=perms)
    monkeypatch.setattr(crypto_service, "build_crypto_client", lambda ex, k, s: b)
    with pytest.raises(crypto_service.CryptoConnectError, match=needle):
        crypto_service.connect(ConfigStore(db), "kraken", "K", "S")
    assert not crypto_service.is_connected(ConfigStore(db))
    assert not (ConfigStore(db).get("crypto_config", {}) or {}).get("api_key")  # refused keys are not stored


def test_unverified_env_key_is_not_used(db, monkeypatch):
    # A key that only exists in config/env (never verified on connect) is inert.
    ConfigStore(db).set("crypto_config", {"exchange": "kraken", "api_key": "ENVKEY", "api_secret": "ENVSECRET"})
    assert not crypto_service.is_connected(ConfigStore(db))
    assert crypto_service.client_for(ConfigStore(db)) is None
    assert crypto_service.public(ConfigStore(db))["needs_verification"] is True


# ── real-exchange permission checks (CCXT stubs, no network) ─────────────

def _ccxt_broker(monkeypatch, exchange_id):
    monkeypatch.delenv("OPENPF_SANDBOX", raising=False)
    b = crypto_client.CcxtCryptoBroker(exchange_id, "k", "s")
    b._ex.fetch_balance = lambda *a, **k: {"total": {}}
    return b


def test_kraken_probe_reads_permission_denied_as_cannot_withdraw(monkeypatch):
    import ccxt

    b = _ccxt_broker(monkeypatch, "kraken")

    def denied(*a, **k):
        raise ccxt.PermissionDenied("kraken {\"error\":[\"EGeneral:Permission denied\"]}")

    b._ex.privatePostWithdrawInfo = denied
    b._ex.privatePostAddOrder = lambda *a, **k: {"result": {"descr": {"order": "validated"}}}
    perms = b.key_permissions()
    assert perms["can_withdraw"] is False and perms["can_trade"] is True


def test_kraken_probe_reads_any_other_answer_as_can_withdraw(monkeypatch):
    import ccxt

    b = _ccxt_broker(monkeypatch, "kraken")

    def unknown_key(*a, **k):
        raise ccxt.InvalidAddress("kraken EFunding:Unknown withdraw key")

    b._ex.privatePostWithdrawInfo = unknown_key
    b._ex.privatePostAddOrder = lambda *a, **k: {}
    assert b.key_permissions()["can_withdraw"] is True
    assert crypto_service.evaluate_permissions(b.key_permissions())


def test_coinbase_and_binance_permission_apis(monkeypatch):
    cb = _ccxt_broker(monkeypatch, "coinbase")
    cb._ex.v3PrivateGetBrokerageKeyPermissions = lambda *a, **k: {"can_view": True, "can_trade": True, "can_transfer": True}
    assert cb.key_permissions()["can_withdraw"] is True
    cb._ex.v3PrivateGetBrokerageKeyPermissions = lambda *a, **k: {"can_view": True, "can_trade": True, "can_transfer": False}
    assert crypto_service.evaluate_permissions(cb.key_permissions()) == []

    bn = _ccxt_broker(monkeypatch, "binance")
    bn._ex.sapiGetAccountApiRestrictions = lambda *a, **k: {"enableReading": True, "enableSpotAndMarginTrading": True,
                                                             "enableWithdrawals": False, "enableInternalTransfer": True}
    assert bn.key_permissions()["can_withdraw"] is True


def test_ccxt_requests_are_pinned_to_the_exchange_host(monkeypatch):
    b = _ccxt_broker(monkeypatch, "kraken")
    with pytest.raises(CryptoError, match="unexpected host"):
        b._ex.fetch("https://evil.example.com/0/private/Balance", "POST")


# ── proposals: crypto symbols route to the exchange, with costs ──────────

def test_propose_crypto_trade_records_fee_and_slippage(db, broker):
    _connect(db, broker)
    _book(db)
    out = proposal_service.propose_trade(db, side="buy", account_kind="crypto", symbol="btc", notional_gbp=200.0,
                                         rationale="starter")
    assert out["ok"], out
    intent = db.get(TradeIntent, out["intent"]["id"])
    assert intent.status == "proposed" and intent.symbol == "BTC" and intent.instrument_code == "BTC/GBP"
    assert intent.meta["account_kind"] == "crypto"
    c = intent.meta["crypto"]
    assert c["exchange"] == "kraken" and c["fee_rate"] == 0.004
    assert c["fee_gbp"] == pytest.approx(intent.estimated_notional * 0.004, abs=0.01)
    assert c["slippage_gbp"] > 0 and out["intent"]["crypto"]["market"] == "BTC/GBP"
    assert not broker.orders  # proposing never touches the exchange's order endpoint


def test_crypto_symbol_without_account_routes_to_crypto_and_wrong_account_is_explained(db, broker):
    _connect(db, broker)
    _book(db)
    routed = proposal_service.propose_trade(db, side="buy", account_kind="", symbol="ETH", notional_gbp=50.0, rationale="x")
    assert routed["ok"] and routed["intent"]["account_kind"] == "crypto"
    wrong = proposal_service.propose_trade(db, side="buy", account_kind="stocks_isa", symbol="BTC", notional_gbp=50.0,
                                           rationale="x")
    assert not wrong["ok"] and "account_kind='crypto'" in wrong["error"]


def test_crypto_cap_blocks_oversized_proposals(db, broker):
    _connect(db, broker)
    _book(db)  # £10.5k book; default cap 5% = £525
    out = proposal_service.propose_trade(db, side="buy", account_kind="crypto", symbol="BTC", notional_gbp=600.0,
                                         rationale="too much")
    assert not out["ok"] and any(v.startswith("crypto-guard: crypto would be") for v in out["violations"])
    assert db.query(TradeIntent).count() == 0


def test_no_proposals_without_a_connected_exchange(db):
    _book(db)
    out = proposal_service.propose_trade(db, side="buy", account_kind="crypto", symbol="BTC", notional_gbp=20.0, rationale="x")
    assert not out["ok"] and "no crypto exchange is connected" in out["error"]


def test_execute_routes_crypto_to_the_exchange(db, broker):
    _connect(db, broker)
    _book(db)
    ConfigStore(db).set_broker({"broker_mode": "live"})
    out = proposal_service.propose_trade(db, side="buy", account_kind="crypto", symbol="BTC", notional_gbp=120.0, rationale="x")
    intent = execution_service.execute_intent(db, out["intent"]["id"], approve=True)
    assert intent.status == "executed" and intent.broker_order_id == "K-1"
    assert broker.orders == [("BTC", "buy", intent.quantity)]
    assert intent.meta["crypto_fill"]["fee"] == 0.5


# ── one book ─────────────────────────────────────────────────────────────

def test_snapshot_rows_put_crypto_in_the_book(db, broker, monkeypatch):
    _connect(db, broker)
    now = datetime.utcnow()
    accounts, positions, source = crypto_service.snapshot_rows(db, ConfigStore(db), now)
    assert source == "crypto-kraken"
    (acct,) = accounts
    assert acct.account_kind == "crypto" and acct.free_cash == 500.0 and acct.total == pytest.approx(500 + 0.002 * 60000)
    (pos,) = positions
    assert (pos.ticker, pos.instrument_code, pos.average_price) == ("BTC", "BTC/GBP", 50000.0)
    assert pos.ppl == pytest.approx(0.002 * 10000)
    # Connecting isn't a gain: what was already on the exchange is an opening contribution.
    from app.models.entities import CashflowEvent

    (opening,) = db.query(CashflowEvent).filter(CashflowEvent.account_kind == "crypto").all()
    assert opening.type == "DEPOSIT" and opening.amount == pytest.approx(620.0)

    db.add(AccountSnapshot(fetched_at=now, account_kind="invest", currency="GBP", free_cash=100, invested=900, total=1000))
    db.add_all(accounts + positions)
    db.commit()
    from app.services import portfolio_service

    monkeypatch.setattr(portfolio_service, "signal_for_symbol", lambda s: None)
    monkeypatch.setattr(portfolio_service, "estimate_portfolio_beta", lambda *a, **k: 1.0)
    monkeypatch.setattr(portfolio_service, "_instrument_meta_map", lambda db: {})
    snap = portfolio_service.get_portfolio_snapshot(db, display_currency="GBP")
    assert snap["metrics"]["total_value"] == pytest.approx(1000 + 620)
    btc = next(p for p in snap["positions"] if p["account_kind"] == "crypto")
    assert btc["yfinance_ticker"] == "BTC-GBP" and btc["name"] == "Bitcoin"
    assert snap["metrics"]["crypto_weight"] == pytest.approx(120 / 1620)


# ── the Starter crypto play ──────────────────────────────────────────────

def _book_ctx(total=20000.0, crypto_cash=300.0):
    return {"total_gbp": total, "free_cash_gbp": {"invest": 500.0, "crypto": crypto_cash}, "positions": []}


def test_starter_crypto_play_is_small_staged_and_ruled(db, broker, monkeypatch):
    from app.services import crypto_play, plays_service

    _connect(db, broker)
    monkeypatch.setattr(plays_service, "book_context", lambda db: _book_ctx())
    monkeypatch.setattr(plays_service, "basket_volatility", lambda legs: None)
    play = crypto_play.create_starter_play(db, conviction="low", include_eth=True, weeks=10)
    assert play.status == "draft" and play.account_kind == "crypto"
    assert [l["instrument_code"] for l in play.legs] == ["BTC/GBP", "ETH/GBP"]
    sizing = play.sizing
    assert sizing["budget_gbp"] == pytest.approx(200.0)  # 1% of £20k, under the 5% cap
    assert len(sizing["tranches"]) == 10 and sizing["dca"]["per_tranche_gbp"] == pytest.approx(20.0)
    assert any(c["rule"] and c["rule"]["type"] == "drawdown_pct" for c in play.kill_criteria)
    assert "FSCS" in play.thesis and "Capital Gains Tax" in play.thesis

    # High conviction never exceeds the crypto cap.
    store = ConfigStore(db)
    store.set_risk({**store.get_risk(), "max_crypto_pct_of_book": 0.02})
    hi = crypto_play.create_starter_play(db, conviction="high", include_eth=False)
    assert hi.sizing["budget_gbp"] == pytest.approx(400.0)  # 3% wanted → capped at 2%


def test_starter_tranche_is_an_ordinary_play_tranche_proposal(db, broker, monkeypatch):
    from app.services import crypto_play, plays_service

    _connect(db, broker)
    _book(db, invest_total=20000.0, crypto_cash=300.0)
    monkeypatch.setattr(plays_service, "book_context", lambda db: _book_ctx())
    monkeypatch.setattr(plays_service, "basket_volatility", lambda legs: None)
    play = crypto_play.create_starter_play(db, conviction="low", include_eth=True, weeks=8)
    res = plays_service.stage_tranche(db, play)
    assert len(res["intents"]) == 2, res
    intents = [db.get(TradeIntent, i["intent_id"]) for i in res["intents"]]
    for i in intents:
        assert i.status == "proposed" and i.meta["source"] == "play" and i.meta["play_id"] == play.id
        assert i.meta["account_kind"] == "crypto" and i.meta["tranche_index"] == 0 and i.meta["crypto"]["fee_gbp"] >= 0
    assert {i.symbol for i in intents} == {"BTC", "ETH"}
    assert sum(i.estimated_notional for i in intents) == pytest.approx(200 / 8 * 1.0, rel=0.02)
    assert not broker.orders


def test_crypto_holdings_belong_to_the_starter_play(db, broker, monkeypatch):
    from app.services import book_service, crypto_play, plays_service

    _connect(db, broker)
    monkeypatch.setattr(plays_service, "book_context", lambda db: _book_ctx())
    monkeypatch.setattr(plays_service, "basket_volatility", lambda legs: None)
    play = crypto_play.create_starter_play(db)
    plays_service.set_status(play, "active")
    db.commit()
    pos = {"ticker": "BTC", "raw_ticker": "BTC", "instrument_code": "BTC/GBP", "yfinance_ticker": "BTC-GBP",
           "account_kind": "crypto", "value": 120.0, "ppl": 5.0, "weight": 0.01, "price": 60000.0, "name": "Bitcoin"}
    book = book_service.compute_book(db, book={"total_gbp": 12000.0, "free_cash_gbp": {"crypto": 300.0}, "positions": [pos]})
    group = next(g for g in book["groups"] if g.get("play_id") == play.id)
    assert [h["ticker"] for h in group["owned"]] == ["BTC"]
