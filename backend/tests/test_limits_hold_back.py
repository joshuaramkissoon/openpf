"""Limit-blocked asks: propose_trade's structured ``blocked`` info, the
hold-back split (propose what fits, hold the rest), staging a held ask for
tomorrow, and the ``not_before`` gate in execute_intent. In-memory DB, no
network."""

from datetime import datetime, timedelta
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401 — registers tables
from app.models.entities import AccountSnapshot, TradeIntent
from app.services import claude_chat_runtime, execution_service, proposal_service
from app.services.config_store import ConfigStore
from app.services.execution_service import ExecutionError, execute_intent


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)()
    store = ConfigStore(session)
    store.set_risk({**store.get_risk(), "max_single_order_notional": 500.0, "max_daily_notional": 1500.0})
    store.set_broker({**store.get_broker(), "broker_mode": "paper"})
    session.add(AccountSnapshot(fetched_at=datetime.utcnow(), account_kind="invest", currency="GBP",
                                free_cash=50_000, invested=0, total=50_000))
    session.commit()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture()
def market(monkeypatch):
    def resolve(db, symbol, code):
        t = (symbol or code or "").upper()
        return {"instrument_code": f"{t}_US_EQ", "ticker": t, "currency": "USD", "name": t.title(),
                "short_name": t, "verified": True}

    monkeypatch.setattr(proposal_service, "_resolve_instrument", resolve)
    monkeypatch.setattr(proposal_service, "_reference_price", lambda inst: (100.0, "USD"))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount * 0.5)  # £50 a share
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda symbol: 100.0)


def _ask(db, symbol, gbp, **kw):
    return proposal_service.propose_trade(
        db, side="buy", account_kind="invest", symbol=symbol, notional_gbp=gbp, rationale="asked", **kw
    )


def test_blocked_info_is_structured(db, market):
    out = _ask(db, "PLTR", 1000)
    assert out["ok"] is False
    [b] = out["blocked"]
    assert b["rail"] == "max_single_order_notional"
    assert b["limit_gbp"] == 500 and b["requested_gbp"] == 1000 and b["remaining_gbp"] == 500
    assert "per-order limit" in b["message"]
    assert db.query(TradeIntent).count() == 0  # without hold_back nothing is created


def test_user_ask_splits_and_holds_back_over_the_limits(db, market):
    nvda = _ask(db, "NVDA", 500, hold_back=True)
    assert nvda["ok"] and "held_back" not in nvda

    pltr = _ask(db, "PLTR", 1000, hold_back=True)
    assert pltr["ok"]
    assert pltr["intent"]["estimated_notional_gbp"] == pytest.approx(500)
    held = pltr["held_back"]
    assert held["amount_gbp"] == pytest.approx(500)
    assert [b["rail"] for b in held["blocked"]] == ["max_single_order_notional"]
    row = db.get(TradeIntent, held["id"])
    assert row.status == "held" and row.meta["account_kind"] == "invest"
    assert "Proposed £500 now; the other £500 is held back" in pltr["message"]

    # Open proposals count against today's cap: £1,000 proposed, £500 left.
    mu = _ask(db, "MU", 600, hold_back=True)
    assert mu["ok"] and mu["intent"]["estimated_notional_gbp"] == pytest.approx(500)
    assert mu["held_back"]["amount_gbp"] == pytest.approx(100)
    assert [b["rail"] for b in mu["held_back"]["blocked"]] == ["max_single_order_notional", "max_daily_notional"]

    # Daily limit used: nothing fits, the whole ask is held.
    btc = _ask(db, "AMD", 400, hold_back=True)
    assert btc["ok"] is False
    [b] = btc["blocked"]
    assert b["rail"] == "max_daily_notional" and b["used_gbp"] == pytest.approx(1500) and b["remaining_gbp"] == 0
    assert "limit is used" in b["message"]
    assert btc["held_back"]["amount_gbp"] == pytest.approx(400)

    # The chat splices both the proposal and the held-back card.
    import json

    ids = claude_chat_runtime.proposal_ids_from_tool_result("mcp__proposals__propose_trade", json.dumps(pltr))
    assert ids == [pltr["intent"]["id"], held["id"]]
    ids = claude_chat_runtime.proposal_ids_from_tool_result("mcp__proposals__propose_trade", json.dumps(btc))
    assert ids == [btc["held_back"]["id"]]

    # Held asks can't be placed, aren't in the queue, and never touch the limits.
    with pytest.raises(ExecutionError):
        execute_intent(db, held["id"], approve=True)
    assert held["id"] not in {i.id for i in execution_service.list_intents(db)}
    assert ConfigStore(db).get_risk()["max_daily_notional"] == 1500.0


def test_other_violations_are_not_held(db, market):
    assert _ask(db, "NVDA", 500, hold_back=True)["ok"]
    again = _ask(db, "NVDA", 500, hold_back=True)  # a repeat: the duplicate guard, not a limit
    assert again["ok"] is False and "held_back" not in again
    assert any("duplicate" in v for v in again["violations"])
    assert db.query(TradeIntent).filter(TradeIntent.status == "held").count() == 0


def test_stage_held_for_tomorrow(db, market):
    for sym in ("NVDA", "MU", "AMD"):
        assert _ask(db, sym, 500, hold_back=True)["ok"]
    btc = _ask(db, "PLTR", 400, hold_back=True)
    held_id = btc["held_back"]["id"]

    staged = proposal_service.stage_held(db, held_id)
    assert staged["ok"], staged
    row = db.get(TradeIntent, held_id)  # same row: the chat card follows it
    tomorrow = execution_service.utc_today() + timedelta(days=1)
    assert row.status == "proposed" and row.not_before == tomorrow
    assert row.estimated_notional == pytest.approx(400)
    assert row.meta["held"]["staged"] is True
    assert staged["intent"]["not_before"] == tomorrow.isoformat()
    # It's in the queue now, and only once.
    assert proposal_service.stage_held(db, held_id)["ok"] is False


def test_not_before_gate_refuses_until_that_day(db, market, monkeypatch):
    tomorrow = execution_service.utc_today() + timedelta(days=1)
    out = _ask(db, "NVDA", 300, not_before=tomorrow)
    assert out["ok"], out
    iid = out["intent"]["id"]
    with pytest.raises(ExecutionError, match="staged for tomorrow"):
        execute_intent(db, iid, approve=True)
    assert db.get(TradeIntent, iid).status == "proposed"  # not approved early
    with pytest.raises(ExecutionError):
        execution_service.approve_intent(db, iid)

    monkeypatch.setattr(execution_service, "utc_today", lambda: tomorrow)
    done = execute_intent(db, iid, approve=True)
    assert done.status == "executed"


def test_daily_cap_counts_on_the_staged_day(db, market):
    # Today's cap is fully used by placed orders…
    for sym in ("NVDA", "MU", "AMD"):
        iid = _ask(db, sym, 500)["intent"]["id"]
        execute_intent(db, iid, approve=True)
    assert not _ask(db, "PLTR", 100)["ok"]  # today: over the daily limit

    # …but tomorrow's window is separate, and counts what's staged for it.
    tomorrow = execution_service.utc_today() + timedelta(days=1)
    for sym in ("PLTR", "META", "TSLA"):
        assert _ask(db, sym, 450, not_before=tomorrow)["ok"]
    over = _ask(db, "AAPL", 200, not_before=tomorrow)
    assert over["ok"] is False
    [b] = over["blocked"]
    assert b["rail"] == "max_daily_notional" and b["used_gbp"] == pytest.approx(1350)
    assert b["for_day"] == tomorrow.isoformat() and "tomorrow's" in b["message"]
    assert _ask(db, "AAPL", 150, not_before=tomorrow)["ok"]


def test_staged_proposals_dont_expire_or_get_superseded_early(db, market):
    tomorrow = execution_service.utc_today() + timedelta(days=1)
    iid = _ask(db, "NVDA", 300, not_before=tomorrow)["intent"]["id"]
    row = db.get(TradeIntent, iid)
    row.created_at = datetime.utcnow() - timedelta(hours=30)
    db.commit()
    execution_service.expire_stale_intents(db)
    execution_service.supersede_open_proposals(db)
    assert db.get(TradeIntent, iid).status == "proposed"
