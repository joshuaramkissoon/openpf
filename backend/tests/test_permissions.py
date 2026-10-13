"""Standing permissions: the only way Archie acts on his own, and only inside
what the user granted. Isolated in-memory DB, no broker, no network."""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.database import Base
from app.models import entities  # noqa: F401
from app.models.entities import (
    AccountSnapshot,
    ExecutionEvent,
    PermissionEvent,
    Play,
    StandingPermission,
    TradeIntent,
)
from app.services import execution_service
from app.services import permissions_service as ps
from app.services.config_store import ConfigStore


@pytest.fixture()
def factory(tmp_path):
    # File-backed so separate sessions/threads really contend (races).
    engine = create_engine(f"sqlite:///{tmp_path}/perm.db", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(bind=engine)
    yield sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture()
def db(factory, monkeypatch):
    monkeypatch.setattr(execution_service, "_paper_fill_price", lambda symbol: 100.0)
    s = factory()
    store = ConfigStore(s)
    store.set_broker({"broker_mode": "paper"})
    store.set_risk({**store.get_risk(), "max_single_order_notional": 5000.0, "max_daily_notional": 50000.0,
                    "duplicate_order_window_seconds": 15})
    s.add(AccountSnapshot(account_kind="stocks_isa", free_cash=100000.0, total=100000.0))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _approved_play(db, play_id="p1", legs=("NVDA", "AVGO", "TSM", "AMD", "MU")):
    play = Play(id=play_id, title="AI chips", status="active",
                legs=[{"ticker": t, "instrument_code": f"{t}_US_EQ"} for t in legs])
    db.add(play)
    # The user approved and placed a trade in this play before.
    db.add(TradeIntent(status="executed", symbol=legs[0], instrument_code=f"{legs[0]}_US_EQ", side="buy",
                       quantity=1, estimated_notional=100.0, approved_by="human",
                       created_at=datetime.utcnow() - timedelta(days=10),
                       meta={"play_id": play_id, "source": "play", "account_kind": "stocks_isa"}))
    db.commit()
    return play


_QTY = iter(range(1, 10_000))


def _tranche(db, *, symbol="AVGO", notional=200.0, play_id="p1", created_at=None, **meta):
    intent = TradeIntent(
        status="proposed", symbol=symbol, instrument_code=f"{symbol}_US_EQ", side="buy",
        quantity=float(next(_QTY)), estimated_notional=notional,
        created_at=created_at or datetime.utcnow(),
        meta={"play_id": play_id, "source": "play", "account_kind": "stocks_isa", "tranche_index": 1,
              "category": "play_tranche", **meta},
    )
    db.add(intent)
    db.commit()
    return intent


def _grant(db, **kw):
    args = {"kind": "play_tranche", "per_trade_cap_gbp": 300, "daily_cap_gbp": 1000}
    args.update(kw)
    perm = ps.create_permission(db, **args)
    # Permissions only act on proposals made after the grant.
    perm.created_at = datetime.utcnow() - timedelta(minutes=5)
    db.commit()
    return perm


# ── the happy path goes through execute_intent ───────────────────────────────


def test_matching_tranche_is_approved_by_permission_and_executed_through_the_gate(db, monkeypatch):
    _approved_play(db)
    perm = _grant(db)
    intent = _tranche(db)
    calls = []
    real = execution_service.execute_intent
    monkeypatch.setattr(execution_service, "execute_intent", lambda d, i, **k: calls.append(i) or real(d, i, **k))

    res = ps.try_auto_approve(db, intent.id)

    assert res["applied"] is True
    assert calls == [intent.id]  # the SAME gate a human approval uses
    db.refresh(intent)
    assert intent.status == "executed"
    assert intent.approved_by == f"permission:{perm.id}"
    approved = [e for e in db.query(ExecutionEvent).all() if e.message == "intent approved"]
    assert approved[-1].payload["actor"] == f"permission:{perm.id}"
    used = db.query(PermissionEvent).filter_by(kind="used").all()
    assert [u.intent_id for u in used] == [intent.id]


def test_digest_reports_only_when_something_happened(db):
    assert ps.digest(db)["count"] == 0 and ps.digest(db)["line"] == ""
    _approved_play(db)
    _grant(db)
    ps.try_auto_approve(db, _tranche(db, symbol="AVGO", notional=200).id)
    ps.try_auto_approve(db, _tranche(db, symbol="TSM", notional=250).id)
    d = ps.digest(db)
    assert d["count"] == 2 and d["notional_gbp"] == 450
    assert d["line"] == "Archie placed 2 tranches under your permissions today: £450"


# ── anything outside the grant still needs a human ───────────────────────────


@pytest.mark.parametrize(
    "case",
    ["over_cap", "uncovered_category", "not_a_leg", "unapproved_play", "sell", "old_proposal", "before_grant"],
)
def test_outside_the_grant_needs_a_human(db, case):
    _approved_play(db)
    _approved_play(db, "p2", legs=("XOM",))
    db.get(Play, "p2").status = "draft"
    db.commit()
    perm = _grant(db)
    kw = {}
    if case == "over_cap":
        kw = {"notional": 301.0}
    elif case == "uncovered_category":
        kw = {"category": "news_reaction", "source": "chat", "tranche_index": None}
    elif case == "not_a_leg":
        kw = {"symbol": "TSLA"}
    elif case == "unapproved_play":
        kw = {"symbol": "XOM", "play_id": "p2"}
    elif case == "old_proposal":
        kw = {"created_at": datetime.utcnow() - timedelta(hours=7)}
    elif case == "before_grant":
        kw = {"created_at": perm.created_at - timedelta(minutes=1)}
    intent = _tranche(db, **kw)
    if case == "sell":
        intent.side = "sell"
        db.commit()
    res = ps.try_auto_approve(db, intent.id)
    assert res["applied"] is False
    db.refresh(intent)
    assert intent.status == "proposed" and intent.approved_by is None


def test_rebalance_permission_only_covers_rebalancer_trades(db):
    perm = _grant(db, kind="rebalance")
    own = TradeIntent(status="proposed", symbol="PLTR", instrument_code="PLTR_US_EQ", side="sell", quantity=1,
                      estimated_notional=100.0, meta={"source": "rebalance", "account_kind": "invest"})
    fake = TradeIntent(status="proposed", symbol="PLTR", instrument_code="PLTR_US_EQ", side="sell", quantity=2,
                       estimated_notional=100.0, meta={"source": "chat", "category": "rebalance",
                                                       "account_kind": "stocks_isa"})
    db.add_all([own, fake])
    db.commit()
    assert ps.evaluate(db, fake)["permission_id"] is None
    assert ps.evaluate(db, own)["permission_id"] == perm.id


def test_propose_trade_cannot_claim_the_rebalance_source(db, monkeypatch):
    from app.services import proposal_service

    monkeypatch.setattr(proposal_service, "_resolve_instrument", lambda d, s, c: {
        "instrument_code": "AAPL_US_EQ", "ticker": "AAPL", "currency": "USD", "name": "Apple",
        "short_name": "AAPL", "verified": True})
    monkeypatch.setattr(proposal_service, "_reference_price", lambda inst: (200.0, "USD"))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount * 0.5)
    out = proposal_service.propose_trade(db, side="buy", account_kind="stocks_isa", symbol="AAPL",
                                         notional_gbp=50.0, rationale="x", source="rebalance")
    assert out["ok"] and db.get(TradeIntent, out["intent"]["id"]).meta["source"] == "agent"


# ── paused / expired / revoked / kill switch ─────────────────────────────────


@pytest.mark.parametrize("state", ["paused", "expired", "revoked", "kill_switch"])
def test_inactive_permissions_do_not_apply(db, state):
    _approved_play(db)
    perm = _grant(db)
    if state == "paused":
        ps.update_permission(db, perm.id, {"enabled": False})
    elif state == "expired":
        perm.expires_at = datetime.utcnow() - timedelta(minutes=1)
        db.commit()
    elif state == "revoked":
        ps.revoke_permission(db, perm.id)
    else:
        ps.set_kill_switch(db, True)
    intent = _tranche(db)
    assert ps.try_auto_approve(db, intent.id)["applied"] is False
    assert ps.process_pending(db) == []
    db.refresh(intent)
    assert intent.status == "proposed"


def test_expiry_in_the_past_is_refused(db):
    with pytest.raises(ps.PermissionError_):
        ps.create_permission(db, kind="play_tranche", per_trade_cap_gbp=100, daily_cap_gbp=100,
                             expires_at=datetime.utcnow() - timedelta(days=1))


def test_daily_cap_below_per_trade_cap_is_refused(db):
    with pytest.raises(ps.PermissionError_):
        ps.create_permission(db, kind="play_tranche", per_trade_cap_gbp=500, daily_cap_gbp=100)


# ── live mode needs an explicit confirmation made in live mode ───────────────


def test_live_mode_requires_live_confirmation(db, monkeypatch):
    _approved_play(db)
    perm = _grant(db)  # granted in paper mode
    ConfigStore(db).set_broker({"broker_mode": "live"})
    intent = _tranche(db)
    v = ps.evaluate(db, intent)
    assert v["permission_id"] is None
    assert "needs live confirmation" in v["reasons"][perm.id]

    # A confirmation can't be given from paper mode.
    ConfigStore(db).set_broker({"broker_mode": "paper"})
    with pytest.raises(ps.PermissionError_):
        ps.confirm_live(db, perm.id)

    ConfigStore(db).set_broker({"broker_mode": "live"})
    ps.confirm_live(db, perm.id)
    assert ps.evaluate(db, intent)["permission_id"] == perm.id

    # Editing the terms clears the confirmation.
    ps.update_permission(db, perm.id, {"per_trade_cap_gbp": 400, "daily_cap_gbp": 1000})
    assert db.get(StandingPermission, perm.id).confirmed_live_at is None
    assert ps.evaluate(db, intent)["permission_id"] is None


def test_create_in_live_mode_without_confirm_does_not_apply(db):
    ConfigStore(db).set_broker({"broker_mode": "live"})
    perm = ps.create_permission(db, kind="play_tranche", per_trade_cap_gbp=100, daily_cap_gbp=300)
    assert perm.confirmed_live_at is None
    confirmed = ps.create_permission(db, kind="rebalance", per_trade_cap_gbp=100, daily_cap_gbp=300,
                                     confirm_live=True)
    assert confirmed.confirmed_live_at is not None


# ── caps under races ─────────────────────────────────────────────────────────


def test_daily_cap_holds_across_sequential_approvals(db):
    _approved_play(db)
    _grant(db, per_trade_cap_gbp=300, daily_cap_gbp=500)
    a = _tranche(db, symbol="AVGO", notional=250)
    b = _tranche(db, symbol="TSM", notional=250)
    c = _tranche(db, symbol="AMD", notional=250)
    results = [ps.try_auto_approve(db, i.id)["applied"] for i in (a, b, c)]
    assert results == [True, True, False]


def test_daily_cap_and_single_approval_hold_under_concurrent_sweeps(factory, db):
    _approved_play(db)
    perm = _grant(db, per_trade_cap_gbp=300, daily_cap_gbp=500)
    ids = [_tranche(db, symbol=s, notional=200).id for s in ("AVGO", "TSM", "AMD", "MU")]
    db.close()

    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def sweep():
        s = factory()
        try:
            barrier.wait()
            ps.process_pending(s)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            s.close()

    threads = [threading.Thread(target=sweep) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors

    s = factory()
    rows = [s.get(TradeIntent, i) for i in ids]
    auto = [r for r in rows if (r.approved_by or "").startswith("permission:")]
    assert sum(r.estimated_notional for r in auto) <= 500
    assert len(auto) == 2  # 2 x £200 fit under £500; a third would not
    assert all(r.status == "executed" for r in auto)
    used = s.query(PermissionEvent).filter_by(kind="used").all()
    assert len(used) == len({u.intent_id for u in used}) == 2  # no proposal approved twice
    assert s.get(StandingPermission, perm.id).spent_today_gbp == pytest.approx(400)
    s.close()


def test_failed_execution_hands_back_to_the_human_and_refunds(db, monkeypatch):
    _approved_play(db)
    perm = _grant(db)
    intent = _tranche(db)

    def boom(d, i, **k):
        raise execution_service.ExecutionError("risk-guard: insufficient available cash")

    monkeypatch.setattr(execution_service, "execute_intent", boom)
    res = ps.try_auto_approve(db, intent.id)
    assert res["applied"] is False
    db.refresh(intent)
    assert intent.status == "proposed" and intent.approved_by is None
    assert db.get(StandingPermission, perm.id).spent_today_gbp == 0


def test_rails_still_apply_to_permitted_trades(db):
    _approved_play(db)
    _grant(db)
    store = ConfigStore(db)
    store.set_risk({**store.get_risk(), "max_single_order_notional": 100.0})
    intent = _tranche(db, notional=200)
    assert ps.try_auto_approve(db, intent.id)["applied"] is False


# ── suggestions grant nothing ────────────────────────────────────────────────


def test_suggestion_needs_a_record_and_grants_nothing(db):
    from app.models.entities import ProposalOutcome

    assert ps.record_suggestion(db, kind="play_tranche")["ok"] is False  # no record yet
    for i in range(8):
        db.add(ProposalOutcome(intent_id=f"i{i}", proposed_at=datetime.utcnow(), category="play_tranche",
                               fate="taken", symbol="X", side="buy", notional_gbp=300.0,
                               marks={"1m": {"dir_ret": 0.05, "excess": 0.02 if i < 6 else -0.01}}))
    db.commit()
    out = ps.record_suggestion(db, kind="play_tranche", note="tranches have worked")
    assert out["ok"], out
    assert "beaten SPY 6 of 8 times" in out["suggestion"]["message"]
    assert "£300 each" in out["suggestion"]["message"]
    assert db.query(StandingPermission).count() == 0
    # Not asked twice in a row.
    assert ps.record_suggestion(db, kind="play_tranche")["ok"] is False


# ── HTTP API (the UI's path; the only way to grant) ──────────────────────────


def test_permissions_api_round_trip():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/permissions", json={"kind": "play_tranche", "per_trade_cap_gbp": 300, "daily_cap_gbp": 900})
        assert r.status_code == 200, r.text
        perm = r.json()
        assert perm["applies"] is True and perm["label"] == "Play tranches up to £300"
        bad = client.post("/api/permissions", json={"kind": "anything", "per_trade_cap_gbp": 1, "daily_cap_gbp": 1})
        assert bad.status_code == 422
        r = client.patch(f"/api/permissions/{perm['id']}", json={"enabled": False})
        assert r.json()["status"] == "paused"
        r = client.put("/api/permissions/kill-switch", json={"paused": True})
        assert r.json()["paused"] is True
        client.put("/api/permissions/kill-switch", json={"paused": False})
        r = client.post(f"/api/permissions/{perm['id']}/revoke")
        assert r.json()["status"] == "revoked"
        kinds = [e["kind"] for e in client.get(f"/api/permissions/{perm['id']}/history").json()]
        assert kinds == ["revoked", "paused", "granted"]
        assert client.get("/api/permissions/digest").json()["count"] == 0
        tr = client.get("/api/track-record").json()
        assert {"headline", "comparison", "categories", "items"} <= set(tr)
