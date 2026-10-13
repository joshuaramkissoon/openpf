"""Play rebalance: the trade maths (bands, min size, per-account funding), the
proposal path with the rebalancer's reserved source, and that the hand-placed
path is out of every agent's reach. No network: holdings and prices are injected."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401 — register all tables
from app.models.entities import Play, TradeIntent
from app.services import claude_sdk_config, mcp_registry
from app.services import play_rebalance as rb


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


LEGS = [
    {"ticker": "AAA", "instrument_code": "AAA_US_EQ", "name": "Alpha", "role": "core", "target_weight": 0.5, "currency": "USD"},
    {"ticker": "BBB", "instrument_code": "BBB_US_EQ", "name": "Beta", "role": "core", "target_weight": 0.3, "currency": "USD"},
    {"ticker": "CCC.L", "instrument_code": "CCCl_EQ", "name": "Gamma", "role": "satellite", "target_weight": 0.2, "currency": "GBP"},
]


def _play(db, legs=LEGS, status="active") -> Play:
    play = Play(title="Test theme", status=status, conviction="medium", account_kind="stocks_isa", budget_gbp=1000,
                legs=[dict(l) for l in legs], sizing={}, kill_criteria=[], review={}, meta={},
                activated_at=datetime.utcnow() - timedelta(days=10))
    db.add(play)
    db.commit()
    return play


def _pos(ticker, value, account="stocks_isa", qty=None, code=None):
    return {"ticker": ticker.split(".")[0], "raw_ticker": ticker.split(".")[0], "instrument_code": code or f"{ticker}_US_EQ",
            "yfinance_ticker": ticker, "name": ticker, "account_kind": account, "value": float(value), "ppl": 0.0,
            "weight": 0.0, "price": 10.0, "quantity": qty if qty is not None else value / 10.0, "currency": "USD"}


def _book(*positions, cash=None):
    return {"total_gbp": 100_000.0, "free_cash_gbp": cash or {"stocks_isa": 0.0, "invest": 0.0}, "positions": list(positions)}


def _plan(db, play, positions, **kw):
    book = _book(*positions, cash=kw.pop("cash", None))
    return rb.plan(db, play, book=book, owned=book["positions"], price_fn=lambda t: 10.0, **kw)


# ── The maths ────────────────────────────────────────────────────────────────

def test_sells_the_overweight_leg_to_fund_the_underweight_one(db):
    play = _play(db)
    p = _plan(db, play, [_pos("AAA", 6000), _pos("BBB", 2000), _pos("CCC.L", 2000, code="CCCl_EQ")])
    trades = [(t["side"], t["ticker"], t["account_kind"], t["notional_gbp"]) for t in p["trades"]]
    # AAA 60% → 50%, BBB 20% → 30%, CCC on target: one sell funds one buy, sells listed first.
    assert trades == [("sell", "AAA", "stocks_isa", 1000.0), ("buy", "BBB", "stocks_isa", 1000.0)]
    by = {l["ticker"]: l for l in p["legs"]}
    assert by["AAA"]["after_weight"] == pytest.approx(0.5)
    assert by["BBB"]["after_weight"] == pytest.approx(0.3)
    assert by["CCC.L"]["action"] == "hold"
    assert p["trades"][0]["shares"] == pytest.approx(100.0)  # £1,000 at £10/share
    # 0.15% FX on the two USD orders.
    assert p["est_fees_gbp"] == pytest.approx(3.0)
    assert p["cash_used_gbp"] == 0


def test_ignores_small_drift_and_tiny_orders(db):
    play = _play(db)
    # AAA +0.8 points, BBB -0.8: inside the 1-point band → no trades.
    p = _plan(db, play, [_pos("AAA", 5080), _pos("BBB", 2920), _pos("CCC.L", 2000, code="CCCl_EQ")])
    assert p["trades"] == []
    assert {i["ticker"] for i in p["ignored"]} == {"AAA", "BBB"}
    # A 2-point drift on a £1,000 play is £20: over the band but under the £25 minimum.
    small = _plan(db, play, [_pos("AAA", 520), _pos("BBB", 280), _pos("CCC.L", 200, code="CCCl_EQ")])
    assert small["trades"] == []


def test_buys_are_funded_only_by_cash_in_the_same_account(db):
    play = _play(db)
    # The overweight AAA sits in Invest; the play's account is the ISA with no cash.
    p = _plan(db, play, [_pos("AAA", 6000, account="invest"), _pos("BBB", 2000), _pos("CCC.L", 2000, code="CCCl_EQ")])
    sells = {t["account_kind"]: 0.0 for t in p["trades"]}
    buys = dict(sells)
    for t in p["trades"]:
        (sells if t["side"] == "sell" else buys)[t["account_kind"]] += t["notional_gbp"]
    for acct, spent in buys.items():
        assert spent <= sells.get(acct, 0.0) + 0.01, acct
    buy = next(t for t in p["trades"] if t["side"] == "buy")
    assert buy["account_kind"] == "invest" and "sale proceeds" in buy["note"]


def test_use_cash_tops_up_without_selling(db):
    play = _play(db)
    positions = [_pos("AAA", 5000), _pos("BBB", 1000), _pos("CCC.L", 2000, code="CCCl_EQ")]
    # Self-funded: nothing is overweight enough to sell beyond the band, so buys are capped by the sales.
    own = _plan(db, play, positions)
    assert sum(t["notional_gbp"] for t in own["trades"] if t["side"] == "buy") <= \
        sum(t["notional_gbp"] for t in own["trades"] if t["side"] == "sell") + 0.01
    with_cash = _plan(db, play, positions, use_cash=True, cash={"stocks_isa": 5000.0})
    assert with_cash["cash_used_gbp"] > 0
    assert not [t for t in with_cash["trades"] if t["side"] == "sell"]  # buy-only
    assert all(t["account_kind"] == "stocks_isa" for t in with_cash["trades"] if t["side"] == "buy")


def test_unsaved_weights_are_normalised(db):
    play = _play(db)
    p = _plan(db, play, [_pos("AAA", 5000), _pos("BBB", 3000), _pos("CCC.L", 2000, code="CCCl_EQ")],
              weights={"AAA": 40, "BBB": 40, "CCC.L": 20})
    by = {l["ticker"]: l for l in p["legs"]}
    assert by["AAA"]["target_weight"] == pytest.approx(0.4)
    assert [(t["side"], t["ticker"]) for t in p["trades"]] == [("sell", "AAA"), ("buy", "BBB")]


def test_nothing_held_means_nothing_to_rebalance(db):
    p = _plan(db, _play(db), [])
    assert p["trades"] == [] and "Stage a tranche" in p["empty_reason"]


def test_full_exit_sells_every_share(db):
    play = _play(db, legs=[{**LEGS[0], "target_weight": 1.0}, {**LEGS[1], "target_weight": 0.0}])
    p = _plan(db, play, [_pos("AAA", 5000), _pos("BBB", 1000, qty=7.5)])
    sell = next(t for t in p["trades"] if t["side"] == "sell")
    assert sell["ticker"] == "BBB" and sell["sell_all"] and sell["shares"] == pytest.approx(7.5)


# ── Proposals: the rebalancer's reserved source ─────────────────────────────

@pytest.fixture()
def priced(monkeypatch):
    from app.services import proposal_service

    def resolve(db, symbol, instrument_code):
        code = instrument_code or f"{symbol}_US_EQ"
        return {"instrument_code": code, "ticker": code.split("_")[0], "currency": "USD", "name": code,
                "short_name": code, "verified": True}

    monkeypatch.setattr(proposal_service, "_resolve_instrument", resolve)
    monkeypatch.setattr(proposal_service, "_reference_price", lambda inst: (12.5, "USD"))
    monkeypatch.setattr(proposal_service, "_to_gbp", lambda amount, ccy: amount * 0.8)


def test_propose_records_rebalance_proposals_sells_first(db, priced):
    play = _play(db)
    book = _book(_pos("AAA", 1200), _pos("BBB", 400), _pos("CCC.L", 400, code="CCCl_EQ"))
    res = rb.propose(db, play, book=book, owned=book["positions"], price_fn=lambda t: 10.0)
    assert [p["side"] for p in res["proposed"]] == ["sell", "buy"]
    rows = db.execute(select(TradeIntent).order_by(TradeIntent.created_at)).scalars().all()
    assert {r.status for r in rows} == {"proposed"}
    for r in rows:
        assert r.meta["source"] == "rebalance" and r.meta["play_id"] == play.id
        assert r.rationale.startswith("[rebalance]")


def test_agents_cannot_claim_the_rebalance_source(db, priced):
    from app.services import proposal_service

    res = proposal_service.propose_trade(db, side="buy", account_kind="invest", rationale="sneaky", symbol="AAA",
                                         notional_gbp=50, source="rebalance")
    assert res["ok"]
    assert db.get(TradeIntent, res["intent"]["id"]).meta["source"] == "agent"


def test_closed_play_cannot_be_rebalanced(db):
    with pytest.raises(rb.RebalanceError):
        rb.propose(db, _play(db, status="closed"))


# ── The hand-placed path is human-only ──────────────────────────────────────

def _denied(result) -> bool:
    return (result.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"


def test_agents_have_no_route_to_the_rebalance_endpoints(monkeypatch):
    monkeypatch.setattr(claude_sdk_config, "resolve_t212_env", lambda: {"T212_BASE_ENV": "demo"})
    for context in mcp_registry.ALL_CONTEXTS:
        _, allowed = mcp_registry.build_mcp_servers(context)
        assert not [t for t in allowed if "play_rebalance" in t or "rebalance_place" in t], context
    for path in mcp_registry.MCP_SERVER_DIR.glob("*.py"):
        src = path.read_text()
        assert "play_rebalance" not in src and "/rebalance/place" not in src, path.name
    for url in ["http://localhost:8000/api/plays/abc/rebalance/place",
                "https://my-openpf.example/api/plays/abc/rebalance/place",
                "https://my-openpf.example/api/plays/abc/rebalance/propose"]:
        assert _denied(asyncio.run(claude_sdk_config._web_fetch_guard({"tool_input": {"url": url}}, None, None))), url
        cmd = f"curl -X POST {url} -d '{{}}'"
        assert _denied(asyncio.run(claude_sdk_config._bash_guard({"tool_input": {"command": cmd}}, None, None))), url
    cmd = "python -c 'from app.services import play_rebalance; play_rebalance.place(db, p)'"
    assert _denied(asyncio.run(claude_sdk_config._bash_guard({"tool_input": {"command": cmd}}, None, None)))


def test_place_endpoint_requires_the_explicit_user_action(db):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import plays as plays_api
    from app.core.database import get_db

    play = _play(db)
    app = FastAPI()
    app.include_router(plays_api.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    for bad in [{}, {"user_action": {"kind": "manual_trade"}}, {"user_action": {"kind": "proposal", "reviewed": True}}]:
        assert client.post(f"/api/plays/{play.id}/rebalance/place", json=bad).status_code == 422
    assert db.query(TradeIntent).count() == 0


def test_a_held_back_sale_holds_back_the_buys_it_funds(db, priced, monkeypatch):
    from app.services import proposal_service

    real = proposal_service.propose_trade

    def capped(db, **kw):
        if kw["side"] == "sell":
            return {"ok": False, "violations": ["risk-guard: order exceeds max single-order notional"]}
        return real(db, **kw)

    monkeypatch.setattr(proposal_service, "propose_trade", capped)
    play = _play(db)
    book = _book(_pos("AAA", 1200), _pos("BBB", 400), _pos("CCC.L", 400, code="CCCl_EQ"))
    res = rb.propose(db, play, book=book, owned=book["positions"], price_fn=lambda t: 10.0)
    assert res["proposed"] == []
    assert [f["side"] for f in res["failed"]] == ["sell", "buy"]
    assert db.query(TradeIntent).count() == 0
