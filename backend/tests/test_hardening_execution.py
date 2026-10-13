"""Edge cases on the propose → approve → execute path: every failure yields a
clear terminal (or retryable) state, never an intent stuck in 'executing'."""

from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import AccountSnapshot, PositionSnapshot, TradeIntent
from app.services import execution_service, proposal_service, sandbox_broker, telegram_service
from app.services.config_store import ConfigStore
from app.services.execution_service import ExecutionError, execute_intent
from app.services.t212_client import T212Error, T212RateLimitError


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    ConfigStore(session).set_broker({"broker_mode": "paper"})
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _intent(db, **overrides):
    intent = TradeIntent(
        status="approved", symbol="AAPL", instrument_code="AAPL_US_EQ", side="sell", order_type="market",
        quantity=1.0, estimated_notional=10.0, meta={"account_kind": "invest"},
    )
    for k, v in overrides.items():
        setattr(intent, k, v)
    db.add(intent)
    db.commit()
    db.refresh(intent)
    return intent


def _snapshot(db, account="invest", cash=1000.0, positions=()):
    now = datetime.utcnow()
    db.add(AccountSnapshot(fetched_at=now, account_kind=account, currency="GBP", free_cash=cash))
    for code, qty in positions:
        db.add(PositionSnapshot(fetched_at=now, account_kind=account, ticker=code.split("_")[0],
                                instrument_code=code, quantity=qty))
    db.commit()


# ── execution ────────────────────────────────────────────────────────────


def test_unexpected_broker_exception_never_leaves_intent_executing(db, monkeypatch):
    def boom(symbol):
        raise RuntimeError("network on fire")

    monkeypatch.setattr(execution_service, "_paper_fill_price", boom)
    intent = _intent(db)
    with pytest.raises(ExecutionError, match="network on fire"):
        execute_intent(db, intent.id)
    row = db.get(TradeIntent, intent.id)
    assert row.status == "failed"
    assert "network on fire" in row.failure_reason


def test_transport_error_marks_outcome_unknown(db, monkeypatch):
    def boom(symbol):
        raise T212Error("Trading 212 request failed: timed out")

    monkeypatch.setattr(execution_service, "_paper_fill_price", boom)
    intent = _intent(db)
    with pytest.raises(T212Error):
        execute_intent(db, intent.id)
    row = db.get(TradeIntent, intent.id)
    assert row.status == "failed"
    assert "outcome unknown" in row.failure_reason


def test_rate_limit_is_retryable(db, monkeypatch):
    def limited(symbol):
        raise T212RateLimitError("Trading 212 rate limit hit (429)")

    monkeypatch.setattr(execution_service, "_paper_fill_price", limited)
    intent = _intent(db)
    with pytest.raises(T212RateLimitError):
        execute_intent(db, intent.id)
    row = db.get(TradeIntent, intent.id)
    assert row.status == "approved"  # not sent: user can retry
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda s: 5.0)
    assert execute_intent(db, intent.id).status == "executed"


def test_double_execute_places_once(db, monkeypatch):
    calls = []
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda s: calls.append(s) or 5.0)
    intent = _intent(db)
    assert execute_intent(db, intent.id).status == "executed"
    with pytest.raises(ExecutionError, match="already executed"):
        execute_intent(db, intent.id)
    assert len(calls) == 1


def test_claim_lost_to_concurrent_executor(db, monkeypatch):
    """Both callers passed the status check; only the UPDATE winner places."""
    intent = _intent(db)
    real_rails = execution_service.rail_violations

    def racing_rails(*args, **kwargs):
        # Another request claims the intent between our check and our claim.
        db.query(TradeIntent).filter(TradeIntent.id == intent.id).update({"status": "executing"})
        db.commit()
        return real_rails(*args, **kwargs)

    monkeypatch.setattr(execution_service, "rail_violations", racing_rails)
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda s: pytest.fail("placed twice"))
    with pytest.raises(ExecutionError, match="already executing"):
        execute_intent(db, intent.id)


@pytest.mark.parametrize("qty", [0.0, -3.0, float("inf")])
def test_bad_quantity_refused(db, qty):
    intent = _intent(db, quantity=qty)
    with pytest.raises(ExecutionError, match="invalid quantity"):
        execute_intent(db, intent.id)
    assert db.get(TradeIntent, intent.id).status == "approved"


def test_wrong_account_refused(db):
    intent = _intent(db)
    with pytest.raises(ExecutionError, match="invalid account"):
        execute_intent(db, intent.id, account_kind="savings")


def test_sell_more_than_held_blocked(db):
    _snapshot(db, positions=[("AAPL_US_EQ", 0.5)])
    intent = _intent(db, quantity=2.0)
    with pytest.raises(ExecutionError, match="only 0.5 held"):
        execute_intent(db, intent.id)


def test_cash_checked_against_the_target_account(db, monkeypatch):
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda s: 5.0)
    _snapshot(db, account="invest", cash=10.0)
    _snapshot(db, account="stocks_isa", cash=5000.0)
    poor = _intent(db, side="buy", estimated_notional=100.0, meta={"account_kind": "invest"})
    with pytest.raises(ExecutionError, match="insufficient"):
        execute_intent(db, poor.id)
    rich = _intent(db, side="buy", symbol="MSFT", instrument_code="MSFT_US_EQ", estimated_notional=100.0,
                   meta={"account_kind": "stocks_isa"})
    assert execute_intent(db, rich.id).status == "executed"


def test_expired_and_superseded_cannot_be_approved_or_executed(db):
    old = _intent(db, status="proposed")
    old.created_at = datetime.utcnow() - timedelta(hours=48)
    db.commit()
    with pytest.raises(ExecutionError, match="expired"):
        execute_intent(db, old.id, approve=True)
    sup = _intent(db, status="expired", failure_reason="superseded by a newer agent run")
    with pytest.raises(ExecutionError, match="superseded"):
        execution_service.approve_intent(db, sup.id)


def test_cannot_reject_an_executed_intent(db):
    done = _intent(db, status="executed")
    with pytest.raises(ExecutionError, match="already executed"):
        execution_service.reject_intent(db, done.id)
    assert db.get(TradeIntent, done.id).status == "executed"


def test_stuck_executing_is_recovered(db):
    stuck = _intent(db, status="executing")
    stuck.updated_at = datetime.utcnow() - timedelta(hours=1)
    fresh = _intent(db, status="executing", symbol="MSFT")
    db.commit()
    rows = {i.id: i for i in execution_service.list_intents(db)}
    assert rows[stuck.id].status == "failed"
    assert "outcome unknown" in rows[stuck.id].failure_reason
    assert rows[fresh.id].status == "executing"  # may genuinely be in flight
    assert execution_service.recover_stuck_executing(db, older_than_minutes=0) == 1


# ── proposals ────────────────────────────────────────────────────────────


@pytest.fixture()
def priced(monkeypatch):
    monkeypatch.setattr(proposal_service, "_reference_price", lambda inst: (100.0, "USD"))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount)


def _meta(monkeypatch, rows):
    import app.services.portfolio_service as ps

    monkeypatch.setattr(ps, "_instrument_meta_map", lambda db: rows)


def test_fuzzy_name_match_is_not_traded(db, monkeypatch, priced):
    _meta(monkeypatch, {"BITQ_US_EQ": {"ticker": "BITQ_US_EQ", "name": "Bitcoin Depot", "short_name": "BITQ", "currency": "USD"}})
    out = proposal_service.propose_trade(db, side="buy", account_kind="invest", symbol="bitcoin",
                                         quantity=1, rationale="x")
    assert out["ok"] is False and "exact symbol" in out["error"]
    assert out["candidates"][0]["instrument_code"] == "BITQ_US_EQ"
    assert db.query(TradeIntent).count() == 0


def test_cross_exchange_ambiguity_requires_instrument_code(db, monkeypatch, priced):
    _meta(monkeypatch, {
        "SHEL_US_EQ": {"ticker": "SHEL_US_EQ", "name": "Shell (ADR)", "short_name": "SHEL", "currency": "USD"},
        "SHELL_EQ": {"ticker": "SHELl_EQ", "name": "Shell", "short_name": "SHEL", "currency": "GBX"},
    })
    out = proposal_service.propose_trade(db, side="buy", account_kind="invest", symbol="SHEL", quantity=1, rationale="x")
    assert out["ok"] is False and "more than one exchange" in out["error"]
    assert len(out["candidates"]) == 2


def test_leveraged_wrapper_does_not_make_symbol_ambiguous(db, monkeypatch, priced):
    _meta(monkeypatch, {
        "GOOGL_US_EQ": {"ticker": "GOOGL_US_EQ", "name": "Alphabet", "short_name": "GOOGL", "currency": "USD"},
        "GOOGL_EQ": {"ticker": "GOOGL_EQ", "name": "Leverage Shares 1x Alphabet", "short_name": "GOOGL", "currency": "USD"},
    })
    _snapshot(db, cash=1000)
    out = proposal_service.propose_trade(db, side="buy", account_kind="invest", symbol="GOOGL", quantity=1, rationale="x")
    assert out["ok"], out
    assert out["intent"]["instrument_code"] == "GOOGL_US_EQ"


def test_unknown_symbol_fails_clearly(db, monkeypatch, priced):
    _meta(monkeypatch, {"AAPL_US_EQ": {"ticker": "AAPL_US_EQ", "name": "Apple", "short_name": "AAPL", "currency": "USD"}})
    out = proposal_service.propose_trade(db, side="buy", account_kind="invest", symbol="ZZZZQ", quantity=1, rationale="x")
    assert out["ok"] is False and "unknown instrument" in out["error"]


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), 5e9])
def test_bad_sizes_rejected(db, monkeypatch, priced, bad):
    _meta(monkeypatch, {"AAPL_US_EQ": {"ticker": "AAPL_US_EQ", "name": "Apple", "short_name": "AAPL", "currency": "USD"}})
    out = proposal_service.propose_trade(db, side="buy", account_kind="invest", symbol="AAPL", quantity=bad, rationale="x")
    assert out["ok"] is False
    assert db.query(TradeIntent).count() == 0


def test_propose_sell_more_than_held_is_reported(db, monkeypatch, priced):
    _meta(monkeypatch, {"AAPL_US_EQ": {"ticker": "AAPL_US_EQ", "name": "Apple", "short_name": "AAPL", "currency": "USD"}})
    _snapshot(db, positions=[("AAPL_US_EQ", 1.0)])
    out = proposal_service.propose_trade(db, side="sell", account_kind="invest", symbol="AAPL", quantity=3, rationale="x")
    assert out["ok"] is False
    assert any("only 1 held" in v for v in out["violations"])


# ── telegram ─────────────────────────────────────────────────────────────


def test_telegram_prefix_must_be_unique_and_long_enough(db):
    a = _intent(db, id="abcd1111-0000-0000-0000-000000000000")
    _intent(db, id="abcd2222-0000-0000-0000-000000000000", symbol="MSFT")
    assert telegram_service._resolve_intent_by_token(db, "ab") is None
    assert telegram_service._resolve_intent_by_token(db, "abcd") is None  # ambiguous
    assert telegram_service._resolve_intent_by_token(db, "abcd1").id == a.id


def test_telegram_allowlist_tolerates_junk_entries():
    assert telegram_service._is_authorized(42, {"allowed_user_ids": ["oops", " 42 "]}) is True


def test_telegram_ignores_edited_messages(db, monkeypatch):
    store = ConfigStore(db)
    store.set_telegram({"enabled": True, "bot_token": "t", "allowed_user_ids": [1]})
    monkeypatch.setattr(telegram_service, "get_updates", lambda *a, **k: [
        {"update_id": 5, "edited_message": {"text": "/execute abcd", "chat": {"id": 9}, "from": {"id": 1}}},
    ])
    handled = []
    monkeypatch.setattr(telegram_service, "handle_telegram_text", lambda db, text: handled.append(text) or "ok")
    monkeypatch.setattr(telegram_service, "send_message", lambda *a, **k: None)
    telegram_service.process_telegram_updates(db)
    assert handled == []


# ── sandbox broker ───────────────────────────────────────────────────────


@pytest.mark.parametrize("qty", ["nan", float("inf"), "abc", 0])
def test_sandbox_rejects_bad_quantities(qty):
    code = next(iter(sandbox_broker._NAMES))
    with pytest.raises(sandbox_broker.SandboxError) as err:
        sandbox_broker.place_order("invest", "execute", "market", {"ticker": code, "quantity": qty})
    assert err.value.status == 400


def test_sandbox_limit_needs_price():
    code = next(iter(sandbox_broker._NAMES))
    with pytest.raises(sandbox_broker.SandboxError, match="limitPrice"):
        sandbox_broker.place_order("invest", "execute", "limit", {"ticker": code, "quantity": 1})
